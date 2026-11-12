import argparse
import atexit
import json
import multiprocessing
import os
import signal
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


def case_workers(count: int) -> int:
    """How many test cases run at once: CASE_WORKERS, else up to 4 and at most one per core. Each
    case also starts a child process for its booleans, so this is about twice as many processes
    (more than 4 ran the Windows PC out of memory once)."""
    if os.environ.get("CASE_WORKERS"):
        return max(1, int(os.environ["CASE_WORKERS"]))
    return max(1, min(4, os.cpu_count() or 1, count))


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

    problems = {name: result for name, result in results.items() if result != "ok"}
    if problems:
        print(f"Pipeline finished; {len(problems)} of {len(results)} cases did not go through: {problems}")
    else:
        print("Pipeline generated all CAD designs")


if __name__ == "__main__":
    args = parse_args()
    main(args)
