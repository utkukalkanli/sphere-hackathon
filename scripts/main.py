import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))   # also works when the project itself is not installed

from agentic_cad.agent import CadAgent  # noqa: E402
from agentic_cad.config import Settings  # noqa: E402
from agentic_cad.trace import Tracer  # noqa: E402

DEFAULT_INPUTS = REPO_ROOT / "artifacts/inputs"
DEFAULT_OUTPUTS = REPO_ROOT / "artifacts/outputs"


@dataclass
class TestCase:
    name: str  # test_case_<n>
    directory: Path
    description: str  # contents of description.md
    views: list[Path]  # images/view_0_front.png ... view_7_iso_back_left_bottom.png
    context: Path | None  # context.png, only present for parts taken from an assembly


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--output_dir", type=Path, default=DEFAULT_OUTPUTS)
    parser.add_argument("--only", nargs="*", default=None, help="process only these cases, e.g. test_case_3")

    return parser.parse_args()


def load_test_cases(input_dir: Path) -> list[TestCase]:
    test_cases = []
    for case_dir in input_dir.glob("test_case_*"):
        if not (case_dir / "description.md").is_file():
            continue
        context = case_dir / "context.png"
        test_cases.append(TestCase(
            name=case_dir.name,
            directory=case_dir,
            description=(case_dir / "description.md").read_text(),
            views=sorted((case_dir / "images").glob("view_*.png")),
            context=context if context.is_file() else None,
        ))
    return sorted(test_cases, key=lambda tc: int(tc.name.removeprefix("test_case_")))


def main(args):
    started = time.time()
    settings = Settings.from_env()
    test_cases = load_test_cases(args.input_dir)
    if args.only:
        test_cases = [tc for tc in test_cases if tc.name in set(args.only)]
    print(f"Initialising agentic workflow: {len(test_cases)} cases, model={settings.model or '(endpoint default)'}, "
          f"endpoint={settings.base_url}, workers={settings.workers}, budget={settings.time_budget_s:.0f}s", flush=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    tracer = Tracer(meta={"input_dir": str(args.input_dir), "output_dir": str(args.output_dir),
                          "model": settings.model, "max_iters": settings.max_iters})
    agent = CadAgent(settings)

    # every case gets an equal share of the wall clock; `workers` cases run at a time
    workers = max(1, min(settings.workers, len(test_cases) or 1))
    rounds = -(-len(test_cases) // workers) if test_cases else 1
    per_case = settings.time_budget_s / rounds
    hard_deadline = started + settings.time_budget_s

    def process(test_case: TestCase) -> dict:
        deadline = min(hard_deadline, time.time() + per_case)
        print(f"processing {test_case.name}", flush=True)
        summary = agent.generate_cad(
            name=test_case.name,
            case_dir=test_case.directory,
            description=test_case.description,
            views=test_case.views,
            context=test_case.context,
            output_path=args.output_dir / f"{test_case.name}.stp",
            deadline=deadline,
            trace=tracer.case(test_case.name),
        )
        details = " ".join(f"{k}={v}" for k, v in summary.items() if k not in ("name", "error") and v is not None)
        print(f"finished {test_case.name}: {details}" + (f" error={summary['error']}" if summary["error"] else ""),
              flush=True)
        return summary

    summaries = []
    status = "ok"
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(process, tc) for tc in test_cases]):
                summaries.append(future.result())
    except BaseException:
        status = "failed"
        raise
    finally:
        tracer.finish(status)

    missing = [s["name"] for s in summaries if not s["written"]]
    print(f"Pipeline generated {len(summaries) - len(missing)}/{len(test_cases)} CAD designs "
          f"in {time.time() - started:.0f}s" + (f"; missing: {missing}" if missing else ""), flush=True)
    return 1 if missing else 0


if __name__ == "__main__":
    args = parse_args()
    # generated scripts and CAD libraries may only write here when the container runs as non-root
    os.environ.setdefault("TMPDIR", "/tmp")
    sys.exit(main(args))
