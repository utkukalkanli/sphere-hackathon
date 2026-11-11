"""What does the shading step (src/agentic_cad/shading.py) do to the solid of the steps before it?

    uv run python tools/check_shading.py                      # the 24 fixed eval cases, with IoU
    uv run python tools/check_shading.py --only test_case_5   # chosen eval cases
    uv run python tools/check_shading.py --practice           # the 9 practice parts (volume only)
    uv run python tools/check_shading.py --rebuild            # recompute the solids of the earlier steps

The solid after the earlier steps is cached in runs/_shading_cache, so only the shading step runs.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
EVAL = ROOT / "datasets" / "eval"
CACHE = ROOT / "runs" / "_shading_cache"


def run(job: dict) -> dict:
    os.environ["TRACE"] = "0"
    from OCP.BRepTools import BRepTools

    import eval_hull
    from agentic_cad import shading, verify
    from agentic_cad import visual_hull as vh
    from agentic_cad.hull_agent import VisualHullAgent

    name, practice = job["name"], job["practice"]
    case = (ROOT / "artifacts" / "inputs" if practice else EVAL / "inputs") / name
    views = sorted((case / "images").glob("view_*.png"))
    agent = VisualHullAgent()
    try:
        base_path = CACHE / f"{'practice_' if practice else ''}{name}.brep"
        if base_path.is_file() and not job["rebuild"]:
            base = vh.largest_solid(vh.read_brep(str(base_path)))
        else:
            base = agent.build_hull(case.joinpath("description.md").read_text(), views)
            base = agent.cut_holes(base, case)
            base = agent.refine(base, case, views, skip=("shading",))
            BRepTools.Write_s(base, str(base_path))
        dims = dict(zip("xyz", vh.read_dimensions(case)))
        true = vh.read_volume(case)
        started = time.time()
        observed = verify.observe(case, dims)
        proposals = []
        if job["verbose"] and observed:  # what the step would propose on the solid as it is now
            drawings = shading.Drawings(base, observed, dims)
            for view in verify.ORTHO_VIEWS:
                if view in observed and shading.iso_for(view) in observed:
                    proposals += [p.summary() for p in shading.find(view, observed, drawings, dims)]
                    proposals += [p.summary() for p in shading.find_slanted(view, observed, drawings, dims)]
                    proposals += [{**p.summary(), 'kind': 'deep'} for p in shading.find_deep(view, observed, drawings, dims)]
        solid = shading.apply(agent, base, case, views)
        seconds = time.time() - started
        row = {"name": name, "before": vh.volume(base) / true, "after": vh.volume(solid) / true,
               "proposals": proposals, "seconds": seconds, "changed": solid is not base}
        if not practice and job["iou"]:
            row["iou_before"] = eval_hull.sampled_iou(base, name, tuple(dims.values()))
            row["iou_after"] = eval_hull.sampled_iou(solid, name, tuple(dims.values())) if solid is not base else row["iou_before"]
        if job["save"]:
            vh.write_step(vh.tidy(solid), Path(job["save"]) / f"{name}.stp")
        return row
    except Exception as error:
        import traceback
        return {"name": name, "error": repr(error), "trace": traceback.format_exc()}
    finally:
        agent.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", nargs="*", metavar="test_case_<n>")
    parser.add_argument("--practice", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--no-iou", action="store_true")
    parser.add_argument("--save", metavar="DIR", help="write the resulting solids here as .stp")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("-v", "--verbose", action="store_true", help="list every proposed cut")
    args = parser.parse_args()

    import eval_hull
    with open(EVAL / "index.csv", newline="") as f:
        tiers = {f"test_case_{row['n']}": row["tier"] for row in csv.DictReader(f)}
    if args.practice:
        names = args.only or [f"test_case_{n}" for n in range(1, 10)]
    else:
        names = args.only or eval_hull.sample(tiers, 8)
    CACHE.mkdir(parents=True, exist_ok=True)
    if args.save:
        Path(args.save).mkdir(parents=True, exist_ok=True)
    jobs = [{"name": n, "practice": args.practice, "rebuild": args.rebuild, "iou": not args.no_iou,
             "save": args.save, "verbose": args.verbose} for n in names]
    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(run, jobs))

    print(f"{'case':14}{'tier':9}{'vol before':>11}{'after':>8}{'IoU before':>12}{'after':>8}{'cuts':>6}{'s':>6}")
    good = [r for r in rows if "error" not in r]
    for row in rows:
        if "error" in row:
            print(f"{row['name']:14}FAILED {row['error']}\n{row['trace']}")
            continue
        tier = "" if args.practice else tiers[row["name"]]
        iou = f"{row['iou_before']:>12.3f}{row['iou_after']:>8.3f}" if "iou_before" in row else f"{'':>20}"
        print(f"{row['name']:14}{tier:9}{row['before']:>11.3f}{row['after']:>8.3f}{iou}"
              f"{len(row['proposals']):>6}{row['seconds']:>6.1f}")
        if args.verbose:
            for proposal in row["proposals"]:
                print("    ", proposal)
    if good and "iou_before" in good[0]:
        for label in ("simple", "medium", "complex", "all"):
            group = [r for r in good if label == "all" or tiers[r["name"]] == label]
            if group:
                print(f"{label:9}{len(group):>3}  IoU {sum(r['iou_before'] for r in group) / len(group):.4f} -> "
                      f"{sum(r['iou_after'] for r in group) / len(group):.4f}")
        worse = [r["name"] for r in good if r["iou_after"] < r["iou_before"] - 0.005]
        print("worse:", ", ".join(worse) or "none")


if __name__ == "__main__":
    main()
