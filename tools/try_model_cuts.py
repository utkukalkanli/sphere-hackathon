"""Run the model-floors step (src/agentic_cad/model_cuts.py) alone on finished solids.

    uv run python tools/try_model_cuts.py SOLIDS_DIR test_case_7 [...] [--out DIR] [--inputs DIR]

Needs OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL (e.g. from .env). Prints our volume / true volume
before and after, what the model proposed and what was kept; with --out writes the result and the log.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from OCP.STEPControl import STEPControl_Reader  # noqa: E402

from agentic_cad import model_cuts  # noqa: E402
from agentic_cad import visual_hull as vh  # noqa: E402
from agentic_cad.hull_agent import VisualHullAgent  # noqa: E402


def read_step(path: Path):
    reader = STEPControl_Reader()
    reader.ReadFile(str(path))
    reader.TransferRoots()
    return vh.largest_solid(reader.OneShape())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("solids", type=Path)
    parser.add_argument("cases", nargs="+")
    parser.add_argument("--inputs", type=Path, default=ROOT / "artifacts" / "inputs")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    agent = VisualHullAgent()
    try:
        for name in args.cases:
            case = args.inputs / name
            solid = read_step(args.solids / f"{name}.stp")
            target = vh.read_volume(case)
            views = sorted((case / "images").glob("*.png"))
            started = time.time()
            result = model_cuts.apply(agent, solid, case, views)
            print(f"{name}: {vh.volume(solid) / target:.4f} -> {vh.volume(result) / target:.4f} "
                  f"in {time.time() - started:.0f} s")
            print("  ", [e.get("setup_seconds") or e.get("view_error") or (e["view"], e["seconds"]) for e in model_cuts.last_log])
            for entry in model_cuts.last_log[2:]:
                for floor in entry["floors"]:
                    print(f"  {entry['view']:6s} area {floor['area']:2d} floor {floor['floor']:8.2f}: "
                          f"{floor['result']} ({floor['reason']})")
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                vh.write_step(result, args.out / f"{name}.stp")
                (args.out / f"{name}_model.json").write_text(json.dumps(model_cuts.last_log, indent=1))
    finally:
        agent.close()


if __name__ == "__main__":
    main()
