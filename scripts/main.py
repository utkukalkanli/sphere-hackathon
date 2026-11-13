import argparse
import atexit
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from agentic_cad import export
from agentic_cad import trace
from agentic_cad import visual_hull
from agentic_cad.hull_agent import VisualHullAgent
from agentic_cad.trace import CaseTrace, Tracer

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = REPO_ROOT / "artifacts/inputs"
DEFAULT_OUTPUTS = REPO_ROOT / "artifacts/outputs"
# The organisers stop the whole run after 20 minutes; main() shares this much of it among the cases.
# A case with less than MIN_CASE_SECONDS left for its hull keeps its bounding box.
RUN_BUDGET = float(os.environ.get("HULL_RUN_BUDGET", 17 * 60))
# A case's deadline is only checked before each step, so a step started just before it can run on
# (the 9 practice parts took 17.5 minutes with a 17 minute budget). At this point the main process
# stops every worker and ends the run: every file on disk is already a valid solid (export.checkpoint
# puts it in place with one rename), so only the steps still running are lost.
HARD_STOP = float(os.environ.get("HULL_HARD_STOP", 18.5 * 60))
MIN_CASE_SECONDS = 5.0
# The agents (src/agentic_cad/harness, one per case, only with OPENAI_API_KEY and OPENAI_MODEL) start on a
# case when the pipeline is done with it and work until this many seconds after the start of the run.
# Their best candidate replaces a case's file while they work, so a run stopped earlier keeps what it has.
# The organisers allow one hour for the nine parts together (2026-11-12); the image sets 30 minutes:
# in the first full run (55 min) nearly all of the agents' gain had come by then.
HARNESS_SECONDS = float(os.environ.get("HARNESS_SECONDS", 19 * 60))
# HARNESS_ONLY=1: no pipeline at all. Every case keeps its bounding box and its agent starts at once
# (with HARNESS_BASE=0 it is not shown the box either): the agents on their own.
HARNESS_ONLY = os.environ.get("HARNESS_ONLY") == "1"
# HARNESS_PARALLEL=1: the agents start at once and model every part from the brief, the pipeline works on
# the same cases beside them as a fallback and writes to a directory of its own; the agents' process puts
# the better solid of the two into the output file (src/agentic_cad/harness/loop.py).
HARNESS_PARALLEL = os.environ.get("HARNESS_PARALLEL") == "1" and not HARNESS_ONLY
HARNESS_DIR = Path(os.environ.get("HARNESS_DIR", "/tmp/harness"))


@dataclass
class TestCase:
    name: str  # test_case_<n>
    description: str  # contents of description.md
    views: list[Path]  # images/view_0_front.png ... view_7_iso_back_left_bottom.png
    context: Path | None  # context.png, only present for parts taken from an assembly


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--output_dir", type=Path, default=DEFAULT_OUTPUTS)
    parser.add_argument("--only", nargs="*", default=None, metavar="test_case_<n>",
                        help="development only: process just these test cases")

    return parser.parse_args()


def load_test_cases(input_dir: Path) -> list[TestCase]:
    test_cases = []
    for case_dir in input_dir.glob("test_case_*"):
        if not (case_dir / "description.md").is_file():
            continue
        context = case_dir / "context.png"
        test_cases.append(TestCase(
            name=case_dir.name,
            description=(case_dir / "description.md").read_text(),
            views=sorted((case_dir / "images").glob("view_*.png")),
            context=context if context.is_file() else None,
        ))
    return sorted(test_cases, key=lambda tc: int(tc.name.removeprefix("test_case_")))


def write_fallback(test_case: TestCase, output_path: Path) -> bool:
    """The plain bounding box: a valid solid of the right size scores something, a missing file 0."""
    try:
        case_dir = test_case.views[0].parent.parent if test_case.views else None
        x, y, z = visual_hull.read_dimensions(case_dir, test_case.description)
        return export.checkpoint(visual_hull.box({"x": x, "y": y, "z": z}), output_path, keep_faces=True)
    except Exception:
        traceback.print_exc()
        return False


MEMORY_PER_CASE = 2.5 * 1024 ** 3   # bytes a case may need at its peak, with its boolean child (9 practice parts
                                    # at 4 at a time peaked at 7.4 GB on the GPU box)


def memory_available() -> float | None:
    """Bytes this run may use: the container's limit (cgroup) or the machine's free memory, whichever
    is less. None when neither can be read."""
    found = []
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = Path(path).read_text().strip()
            if value.isdigit() and int(value) < 1 << 60:
                found.append(float(value))
        except OSError:
            pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                found.append(float(line.split()[1]) * 1024)
    except OSError:
        pass
    return min(found) if found else None


def case_workers(count: int) -> int:
    """How many test cases run at once: CASE_WORKERS, else up to 4, at most one per core and at most
    as many as the memory holds. Each case also starts a child process for its booleans, so this is
    about twice as many processes. With too little memory a process is killed, and a killed boolean
    child leaves its case waiting for good (seen in a container limited to 4 GB: after 20 minutes
    four cases were still their bounding boxes)."""
    if os.environ.get("CASE_WORKERS"):
        return max(1, int(os.environ["CASE_WORKERS"]))
    workers = min(4, os.cpu_count() or 1, count)
    memory = memory_available()
    if memory is not None:
        workers = min(workers, int(memory // MEMORY_PER_CASE))
    return max(1, workers)


def size(test_case: TestCase) -> int:
    """The part's face count from facts.json: the cases with most faces take longest, so they start first."""
    try:
        return int(json.loads((test_case.views[0].parent.parent / "facts.json").read_text()).get("faces") or 0)
    except Exception:
        return 0


_STARTED = None   # in a worker: shared count of the cases that have started (see `_init_worker`)


def _init_worker(counter) -> None:
    global _STARTED
    _STARTED = counter
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


def solve(agent: VisualHullAgent, test_case: TestCase, output_path: Path, deadline: float) -> str:
    """Hull, holes and later steps of one case; each step that improved the solid has already
    replaced its file. Never raises. Returns "ok" when this case's own solid is on disk."""
    try:
        if deadline - time.time() < MIN_CASE_SECONDS:
            raise TimeoutError("the run is out of time")
        agent.deadline = deadline
        solid = agent.carve(test_case.description, test_case.views)
        result = "ok" if agent.checkpoint(solid, output_path) else "fallback"
        case_dir = test_case.views[0].parent.parent if test_case.views else None
        solid = agent.refine(solid, case_dir, test_case.views, output_path=output_path)
        agent.finish(solid, output_path)
        return result
    except Exception:
        error = traceback.format_exc()
        print(f"{test_case.name} FAILED, its last good file stays:\n{error}")
        trace.log(error, level="error")
        return "fallback"


def run_case(job: tuple) -> tuple[str, str]:
    """One test case in a worker process. Its time share is set when it starts: the time left,
    shared among the rounds of cases still to start."""
    test_case, output_path, run_dir, started, count, workers = job
    with _STARTED.get_lock():
        index = _STARTED.value
        _STARTED.value += 1
    rounds_left = -(-(count - index) // workers)
    left = RUN_BUDGET - (time.time() - started)
    deadline = time.time() + max(left / max(rounds_left, 1), 0.0)
    agent = VisualHullAgent()
    try:
        tracer = Tracer.attached(Path(run_dir)) if run_dir else None
        with CaseTrace(tracer, test_case.name) as case:
            result = solve(agent, test_case, Path(output_path), deadline)
            case.output(Path(output_path))
        return test_case.name, result
    finally:
        agent.close()


def start_harness(args, started: float, names: list[str]):
    """The agents' process, or None when they are off. It never touches a case before `mark_done`."""
    try:
        from agentic_cad.harness import loop

        if not loop.enabled():
            return None
        work = HARNESS_DIR / time.strftime("%Y%m%d-%H%M%S")
        work.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "agentic_cad.harness.loop", "--input_dir", str(args.input_dir),
                   "--output_dir", str(args.output_dir), "--work_dir", str(work),
                   "--deadline", str(started + HARNESS_SECONDS)]
        if HARNESS_PARALLEL:
            (work / "pipeline").mkdir(exist_ok=True)
            command += ["--pipeline_dir", str(work / "pipeline")]
        command += ["--only", *names]
        return subprocess.Popen(command, start_new_session=True), work
    except Exception:
        traceback.print_exc()
        return None


def mark_done(harness, name: str) -> None:
    """Tell the agents the pipeline has finished a case: its file is final and theirs to improve."""
    if harness is None:
        return
    try:
        folder = harness[1] / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "hull_done").touch()
    except Exception:
        traceback.print_exc()


def finish_harness(harness, started: float) -> None:
    if harness is None:
        return
    process = harness[0]
    try:
        process.wait(timeout=max(started + HARNESS_SECONDS + 20 - time.time(), 1.0))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            pass


def main(args):
    print("Initialising agentic workflow")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    # The directory may hold nothing but test_case_<n>.stp: clear what a killed run left, now and on leaving
    export.clean(args.output_dir)
    atexit.register(export.clean, args.output_dir)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    test_cases = load_test_cases(args.input_dir)
    if args.only:
        test_cases = [tc for tc in test_cases if tc.name in args.only]
    outputs = {tc.name: args.output_dir / f"{tc.name}.stp" for tc in test_cases}
    started = time.time()

    # Every case has a valid file from the first seconds on, and from then on only ever a better one
    # (export.checkpoint): the run can crash or be stopped at any moment.
    # 1. The bounding box of every case. It also replaces a stale file from an earlier run.
    results: dict[str, str] = {}
    for test_case in test_cases:
        if write_fallback(test_case, outputs[test_case.name]):
            results[test_case.name] = "fallback"
        else:
            outputs[test_case.name].unlink(missing_ok=True)  # not this run's answer
            results[test_case.name] = "failed"

    # 2. Every case from hull to last step, several at once: one case uses one core, and the run has
    # 20 minutes for all of them. The largest parts start first. A worker that dies leaves the file
    # of that case's last good step.
    workers = case_workers(len(test_cases))
    order = sorted(test_cases, key=lambda tc: -size(tc))
    # Records every step under runs/ for the dashboard (ui/); a no-op where it can't write
    with Tracer(meta={"input_dir": args.input_dir, "output_dir": args.output_dir, "workers": workers}) as tracer:
        run_dir = str(tracer.dir) if tracer.dir else None
        jobs = [(tc, str(outputs[tc.name]), run_dir, started, len(order), workers) for tc in order]
        print(f"processing {len(jobs)} test cases, {workers} at a time")
        counter = multiprocessing.get_context("spawn").Value("i", 0)
        pool = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn"),
                                   initializer=_init_worker, initargs=(counter,))
        stopped = False
        harness = start_harness(args, started, [tc.name for tc in order])
        if HARNESS_PARALLEL and harness is not None:
            # the pipeline's solids go beside the agents' work, not into the output directory
            jobs = [(job[0], str(harness[1] / "pipeline" / f"{job[0].name}.stp"), *job[2:]) for job in jobs]
        if HARNESS_ONLY and harness is not None:
            jobs = []
            for test_case in order:
                results[test_case.name] = "ok" if results.get(test_case.name) == "fallback" else results[test_case.name]
        try:
            futures = {pool.submit(run_case, job): job[0].name for job in jobs}
            for future in as_completed(futures, timeout=max(HARD_STOP - (time.time() - started), 1.0)):
                name = futures[future]
                try:
                    name, result = future.result()
                except Exception:  # its process died (a crash inside OpenCascade): its last file stays
                    print(f"{name} FAILED in its worker process:")
                    traceback.print_exc()
                    continue
                mark_done(harness, name)
                if results.get(name) != "failed" or result == "ok":
                    results[name] = result
                print(f"{name}: {result}")
        except TimeoutError:
            stopped = True
            unfinished = sorted(name for future, name in futures.items() if not future.done())
            print(f"the run reached its hard stop ({HARD_STOP:.0f} s); stopping {unfinished}, "
                  "their last checkpointed files stay")
            for name in unfinished:
                results[name] = "stopped"
            for process in list(getattr(pool, "_processes", {}).values()):
                process.terminate()   # SIGTERM: the worker exits and takes its boolean child with it
        finally:
            pool.shutdown(wait=not stopped, cancel_futures=True)

        # 3. A worker that is killed (out of memory, most likely) breaks the pool, and every case that was
        # running or waiting is left with its box or its last step. Those are run again, one at a time,
        # each in a pool of its own, for as long as the run has time: a single case needs a quarter of
        # the memory, and one that dies again costs only itself.
        again = [job for job in jobs if results.get(job[0].name) not in ("ok", "stopped")] if not stopped else []
        # The agents may start on every case whose file is final: all but the ones about to run again
        # (also the cases the hard stop cut short).
        for test_case in order:
            if test_case.name not in {job[0].name for job in again}:
                mark_done(harness, test_case.name)
        for number, job in enumerate(again):
            # its share of the time: what is left, among the cases still to run again
            with counter.get_lock():
                counter.value = 0
            job = (*job[:4], len(again) - number, 1)
            left = HARD_STOP - (time.time() - started)
            if left < 20:
                print(f"no time left to run {job[0].name} again; its last file stays")
                continue
            print(f"{job[0].name} did not go through; running it again by itself ({left:.0f} s left)")
            single = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"),
                                         initializer=_init_worker, initargs=(counter,))
            waited = True
            try:
                name, result = single.submit(run_case, job).result(timeout=left)
                results[name] = result
                print(f"{name}: {result}")
            except TimeoutError:
                waited = False
                results[job[0].name] = "stopped"
                for process in list(getattr(single, "_processes", {}).values()):
                    process.terminate()
            except Exception:
                print(f"{job[0].name} FAILED again in its worker process:")
                traceback.print_exc()
            finally:
                single.shutdown(wait=waited, cancel_futures=True)
                mark_done(harness, job[0].name)
        for test_case in order:
            mark_done(harness, test_case.name)
        finish_harness(harness, started)
        if HARNESS_PARALLEL and harness is not None:
            # a case the agents' process never wrote (it died, or never got that far) gets the pipeline's solid
            for test_case in order:
                side = harness[1] / "pipeline" / f"{test_case.name}.stp"
                if not (harness[1] / test_case.name / "published").exists() and export.file_valid(side):
                    export.publish(side, outputs[test_case.name])
                    print(f"{test_case.name}: the agents left no file, the pipeline's solid is the answer")

    problems = {name: result for name, result in results.items() if result != "ok"}
    if problems:
        print(f"Pipeline finished; {len(problems)} of {len(results)} cases did not go through: {problems}")
    else:
        print("Pipeline generated all CAD designs")


if __name__ == "__main__":
    args = parse_args()
    main(args)
