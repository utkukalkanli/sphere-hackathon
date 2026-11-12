import argparse
import atexit
import os
import signal
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

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


def main(args):
    print("Initialising agentic workflow")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    # The directory may hold nothing but test_case_<n>.stp: clear what a killed run left, now and on leaving
    export.clean(args.output_dir)
    atexit.register(export.clean, args.output_dir)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    agent = VisualHullAgent()
    test_cases = load_test_cases(args.input_dir)
    if args.only:
        test_cases = [tc for tc in test_cases if tc.name in args.only]
    outputs = {tc.name: args.output_dir / f"{tc.name}.stp" for tc in test_cases}
    started = time.time()

    def share(cases_left: int) -> float:
        return (RUN_BUDGET - (time.time() - started)) / cases_left

    # Every case has a valid file from the first seconds on, and from then on only ever a better one:
    # the run can crash or be stopped at any moment. So the passes go over all cases in turn.
    # 1. The bounding box of every case. It also replaces a stale file from an earlier run.
    results: dict[str, str] = {}
    for test_case in test_cases:
        if write_fallback(test_case, outputs[test_case.name]):
            results[test_case.name] = "fallback"
        else:
            outputs[test_case.name].unlink(missing_ok=True)  # not this run's answer
            results[test_case.name] = "failed"

    # Records every step under runs/ for the dashboard (ui/); a no-op where it can't write
    with Tracer(meta={"input_dir": args.input_dir, "output_dir": args.output_dir}) as tracer:
        traces = {tc.name: tracer.case(tc.name) for tc in test_cases}

        # 2. Hull and holes of every case: seconds each, and most of what a case scores
        carved = []  # (test case, solid, what the agent needs back for the later steps)
        for index, test_case in enumerate(test_cases):
            print(f"processing {test_case.name}")
            case, output_path = traces[test_case.name], outputs[test_case.name]
            case.more = True
            with case:
                try:
                    left = share(len(test_cases) - index)
                    if left < MIN_CASE_SECONDS:
                        raise TimeoutError("the run is out of time")
                    agent.deadline = time.time() + left
                    solid = agent.carve(test_case.description, test_case.views)
                    if agent.checkpoint(solid, output_path):
                        results[test_case.name] = "ok"
                    carved.append((test_case, solid, agent.state()))
                except Exception:
                    error = traceback.format_exc()
                    print(f"{test_case.name} FAILED, its bounding box stays:\n{error}")
                    trace.log(error, level="error")
                    case.more = False
                    case.output(output_path)

        # 3. The later steps, where there is most to gain first: that only matters when time runs short.
        # The time left is shared equally among the cases still to do; a case past its share skips the
        # rest of its steps and keeps the file of the last one that went through.
        carved.sort(key=lambda entry: -excess(entry[0], entry[1]))
        for index, (test_case, solid, state) in enumerate(carved):
            print(f"refining {test_case.name}")
            case, output_path = traces[test_case.name], outputs[test_case.name]
            case.more = False
            with case:
                try:
                    agent.resume(state)
                    agent.deadline = time.time() + max(share(len(carved) - index), 0.0)
                    case_dir = test_case.views[0].parent.parent if test_case.views else None
                    solid = agent.refine(solid, case_dir, test_case.views, output_path=output_path)
                    agent.finish(solid, output_path)
                    if output_path.is_file():
                        results[test_case.name] = "ok"
                except Exception:
                    error = traceback.format_exc()
                    print(f"{test_case.name}: the later steps FAILED, the file of the last good step stays:\n{error}")
                    trace.log(error, level="error")
                case.output(output_path)

    agent.close()
    problems = {name: result for name, result in results.items() if result != "ok"}
    if problems:
        print(f"Pipeline finished; {len(problems)} of {len(results)} cases did not go through: {problems}")
    else:
        print("Pipeline generated all CAD designs")


if __name__ == "__main__":
    args = parse_args()
    main(args)
