import argparse
import atexit
import multiprocessing
import os
import signal
import sys
import time
import traceback
from dataclasses import dataclass
from multiprocessing import connection
from pathlib import Path
from types import SimpleNamespace

from agentic_cad import export
from agentic_cad import trace
from agentic_cad import visual_hull
from agentic_cad.hull_agent import VisualHullAgent
from agentic_cad.trace import Tracer

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = REPO_ROOT / "artifacts/inputs"
DEFAULT_OUTPUTS = REPO_ROOT / "artifacts/outputs"
# The organisers stop the whole run after 20 minutes; main() shares this much of it among the cases.
# A case with less than MIN_CASE_SECONDS left for its hull keeps its bounding box.
RUN_BUDGET = float(os.environ.get("HULL_RUN_BUDGET", 17 * 60))
MIN_CASE_SECONDS = 5.0
# In the third pass a case may take this many equal shares of the time left (never more than what is
# left). With one share each, the first cases, which are the ones with the most to gain, were stopped
# at 1/9 of the budget in a run that needs less than half of it, so the output followed the machine's
# speed (practice 7: 110 to 140 s against a share of 113 s). Every case has its file before this pass.
CASE_SHARES = 2.0
# Cases run side by side, each in a process of its own (HULL_WORKERS at a time; by default one fewer
# than the machine's processors, so a machine with two runs them one after another as before). A
# process lives from its case's hull to the end of its later steps and waits in between, holding the
# solid: at most this many are alive at once. A set up to this size gets every hull before any later
# step whatever the number of workers.
OPEN_CASES = 16


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


def excess(test_case: TestCase, solid) -> float:
    """Our volume over the part's true one: how much the later steps have to gain on this case."""
    try:
        return visual_hull.volume(solid) / visual_hull.read_volume(test_case.views[0].parent.parent)
    except Exception:
        return 0.0


def processors() -> int:
    """The processors this run may use: those of the machine, less where a container limits it."""
    try:
        count = len(os.sched_getaffinity(0))
    except AttributeError:  # not on Linux
        count = os.cpu_count() or 1
    try:  # cgroup v2: "<quota> <period>" in microseconds, or "max <period>"
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            count = min(count, max(int(int(quota) / int(period)), 1))
    except Exception:
        pass
    return count


def worker_count(cases: int) -> int:
    try:
        asked = int(os.environ.get("HULL_WORKERS", ""))
    except ValueError:
        asked = processors() - 1
    return max(1, min(asked, cases))


def run_case(test_case: TestCase, output_path: Path, run_dir: Path | None, link) -> None:
    """One case, in a process of its own: the hull and holes when main() says "carve", the later steps
    when it says "refine", each with the seconds it may take. The solid stays in this process between
    the two. Whatever happens here costs this case only; its file is the last one that went through."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so that the agent's own child goes too
    agent = VisualHullAgent()
    case = trace.CaseTrace(SimpleNamespace(dir=run_dir), test_case.name)
    case_dir = test_case.views[0].parent.parent if test_case.views else None
    try:
        _, seconds = link.recv()
        print(f"processing {test_case.name}", flush=True)
        case.more = True
        solid, saved = None, False
        with case:
            try:
                if seconds < MIN_CASE_SECONDS:
                    raise TimeoutError("the run is out of time")
                agent.deadline = time.time() + seconds
                solid = agent.carve(test_case.description, test_case.views)
                saved = agent.checkpoint(solid, output_path)
            except Exception:
                error = traceback.format_exc()
                print(f"{test_case.name} FAILED, its bounding box stays:\n{error}", flush=True)
                trace.log(error, level="error")
                case.more = False
                case.output(output_path)
                solid = None
        link.send({"carved": solid is not None, "saved": saved,
                   "excess": excess(test_case, solid) if solid is not None else 0.0})
        if solid is None:
            return

        _, seconds = link.recv()
        print(f"refining {test_case.name}", flush=True)
        case.more = False
        with case:
            try:
                agent.deadline = time.time() + seconds
                solid = agent.refine(solid, case_dir, test_case.views, output_path=output_path)
                agent.finish(solid, output_path)
            except Exception:
                error = traceback.format_exc()
                print(f"{test_case.name}: the later steps FAILED, the file of the last good step stays:\n{error}",
                      flush=True)
                trace.log(error, level="error")
            case.output(output_path)
        link.send({"refined": True})
    except (EOFError, OSError):  # main() is gone
        pass
    finally:
        agent.close()


def main(args):
    print("Initialising agentic workflow")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    # The directory may hold nothing but test_case_<n>.stp: clear what a killed run left, now and on leaving
    export.clean(args.output_dir)
    atexit.register(export.clean, args.output_dir)
    test_cases = load_test_cases(args.input_dir)
    if args.only:
        test_cases = [tc for tc in test_cases if tc.name in args.only]
    outputs = {tc.name: args.output_dir / f"{tc.name}.stp" for tc in test_cases}
    started = time.time()
    workers = worker_count(len(test_cases))
    processes: dict[str, multiprocessing.Process] = {}

    def stop(*_):
        for process in processes.values():
            if process.is_alive():
                process.terminate()
        sys.exit(143)

    signal.signal(signal.SIGTERM, stop)

    def seconds_for(cases_left: int, shares: float = 1.0) -> float:
        """What a case starting now may take: its share of the time left, of which `workers` pass at once."""
        left = RUN_BUDGET - (time.time() - started)
        return max(min(left, left * workers * min(shares, cases_left) / cases_left), 0.0)

    # Every case has a valid file from the first seconds on, and from then on only ever a better one:
    # the run can crash or be stopped at any moment.
    # 1. The bounding box of every case. It also replaces a stale file from an earlier run.
    results: dict[str, str] = {}
    for test_case in test_cases:
        if write_fallback(test_case, outputs[test_case.name]):
            results[test_case.name] = "fallback"
        else:
            outputs[test_case.name].unlink(missing_ok=True)  # not this run's answer
            results[test_case.name] = "failed"

    # Records every step under runs/ for the dashboard (ui/); a no-op where it can't write
    with Tracer(meta={"input_dir": args.input_dir, "output_dir": args.output_dir, "workers": workers}) as tracer:
        spawn = multiprocessing.get_context("spawn")
        waiting = list(test_cases)  # hull not started, in the order of their numbers
        carved: dict[str, float] = {}  # hull done, later steps not started: our volume over the true one
        busy: dict = {}  # link -> name of a case that is working
        links: dict = {}  # name -> link to its process

        def lost(name: str) -> None:
            print(f"{name}: its process died; the file of the last good step stays", flush=True)
            tracer.case(name).abandon("the process of this case died")
            carved.pop(name, None)

        while waiting or carved or busy:
            # 2. Hull and holes: seconds each, and most of what a case scores. They go first.
            # 3. The later steps, where there is most to gain first: that only matters when time runs
            # short. A case past its time skips the rest of its steps and keeps the file of the last
            # one that went through.
            while len(busy) < workers:
                alive = sum(1 for process in processes.values() if process.is_alive())
                if waiting and alive < OPEN_CASES:
                    test_case = waiting.pop(0)
                    link, far_end = spawn.Pipe()
                    process = spawn.Process(target=run_case, name=test_case.name,
                                            args=(test_case, outputs[test_case.name], tracer.dir, far_end))
                    process.start()
                    far_end.close()
                    processes[test_case.name], links[test_case.name] = process, link
                    order, name = ("carve", seconds_for(len(waiting) + 1)), test_case.name
                elif carved:
                    name = max(carved, key=carved.get)
                    del carved[name]
                    link = links[name]
                    order = ("refine", seconds_for(len(waiting) + len(busy) + len(carved) + 1, CASE_SHARES))
                else:
                    break
                try:
                    link.send(order)
                    busy[link] = name
                except OSError:
                    lost(name)
            if not busy:  # a process that has just finished is still on its way out
                time.sleep(0.05)
                continue
            for link in connection.wait(list(busy)):
                name = busy.pop(link)
                try:
                    told = link.recv()
                except (EOFError, OSError):
                    lost(name)
                    continue
                if told.get("saved") or (told.get("refined") and outputs[name].is_file()):
                    results[name] = "ok"
                if told.get("carved"):
                    carved[name] = told["excess"]

        for process in processes.values():
            process.join()

    problems = {name: result for name, result in results.items() if result != "ok"}
    if problems:
        print(f"Pipeline finished; {len(problems)} of {len(results)} cases did not go through: {problems}")
    else:
        print("Pipeline generated all CAD designs")


if __name__ == "__main__":
    args = parse_args()
    main(args)
