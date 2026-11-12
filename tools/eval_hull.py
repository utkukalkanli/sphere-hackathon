"""Quick local score of the deterministic pipeline on the eval set (datasets/eval).

    uv run python tools/eval_hull.py              # 24 fixed cases, 4 processes; seconds when cached
    uv run python tools/eval_hull.py --all        # all 266 cases (minutes)
    uv run python tools/eval_hull.py --iou        # also measure the real overlap with the ground truth

It is built to be cheap to re-run:
- a fixed sample of 24 cases (8 per tier), not the whole set, unless --all / --cases N / --only;
- results are cached under runs/_eval_cache, keyed by the code that produced them. The solid after
  step 1 (silhouettes, the slow part) has its own cache keyed by visual_hull.py only, so working on
  later steps does not recompute it.

Score: for every step, how well the volume so far agrees with the part's true volume,
min(ours, true) / max(ours, true). It is an upper bound of the overlap (IoU) and equal to it while
our solid still contains the whole part, which holds as long as nothing is cut that should not be.
--iou checks that by sampling points in the bounding box against the ground-truth solid.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "datasets" / "eval"
SRC = ROOT / "src" / "agentic_cad"
CACHE = ROOT / "runs" / "_eval_cache"
STEPS = ["box", "front_back", "right_left", "top_bottom", "iso", "holes", "rounds", "curved", "depth", "shading", "pockets", "split"]
IOU_POINTS = 1500


def digest(*parts) -> str:
    h = hashlib.sha1()
    for part in parts:
        h.update(part.read_bytes() if isinstance(part, Path) else json.dumps(part, sort_keys=True).encode())
    return h.hexdigest()[:16]


def sample(cases: dict[str, str], per_tier: int) -> list[str]:
    """Evenly spaced cases of every tier: the same ones every time."""
    chosen = []
    for tier in ("simple", "medium", "complex"):
        names = [n for n, t in cases.items() if t == tier]
        step = max(1, len(names) // per_tier)
        chosen += names[::step][:per_tier]
    return chosen


def sampled_iou(solid, name: str, dims) -> float:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt, gp_Trsf, gp_Vec
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_IN

    reader = STEPControl_Reader()
    reader.ReadFile(str(EVAL / "ground_truth" / f"{name}.stp"))
    reader.TransferRoots()
    truth = reader.OneShape()
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(truth, box, False, False)
    low = box.CornerMin()
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(-low.X(), -low.Y(), -low.Z()))  # ours has its min corner at the origin
    truth = BRepBuilderAPI_Transform(truth, move, True).Shape()
    rng = random.Random(0)
    points = [gp_Pnt(*(rng.uniform(0, d) for d in dims)) for _ in range(IOU_POINTS)]

    def inside(shape):
        classifier = BRepClass3d_SolidClassifier(shape)
        result = []
        for point in points:
            classifier.Perform(point, 1e-6)
            result.append(classifier.State() == TopAbs_IN)
        return result

    ours, theirs = inside(solid), inside(truth)
    union = sum(a or b for a, b in zip(ours, theirs))
    return sum(a and b for a, b in zip(ours, theirs)) / union if union else 0.0


def evaluate(job: dict) -> dict:
    """One case, in a worker process."""
    os.environ["TRACE"] = "0"
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    from agentic_cad import visual_hull as vh
    from agentic_cad.hull_agent import VisualHullAgent

    name, options = job["name"], job["options"]
    result_path = CACHE / f"result_{job['result_key']}_{name}.json"
    if job["cache"] and result_path.is_file():
        return {**json.loads(result_path.read_text()), "cached": True}

    case = EVAL / "inputs" / name
    views = sorted((case / "images").glob("view_*.png"))
    started = time.time()
    agent = VisualHullAgent(use_iso=options["iso"], use_holes=options["holes"])
    try:
        hull_path = CACHE / f"hull_{job['hull_key']}_{name}.brep"
        volumes_path = hull_path.with_suffix(".json")
        if job["cache"] and hull_path.is_file() and volumes_path.is_file():
            solid = vh.largest_solid(vh.read_brep(str(hull_path)))
            agent.volumes = json.loads(volumes_path.read_text())
        else:
            solid = agent.build_hull(case.joinpath("description.md").read_text(), views)
            BRepTools.Write_s(solid, str(hull_path))
            volumes_path.write_text(json.dumps(agent.volumes))
        if options["holes"]:
            solid = agent.cut_holes(solid, case)
        solid = agent.refine(solid, case, views, skip=tuple(options["skip"]))
        faces, explorer = 0, TopExp_Explorer(solid if agent.keep_faces else vh.tidy(solid), TopAbs_FACE)
        while explorer.More():
            faces += 1
            explorer.Next()
        row = {"name": name, "true": vh.read_volume(case), "volumes": agent.volumes, "faces": faces,
               "seconds": round(time.time() - started, 2)}
        if options["iou"]:
            row["iou"] = round(sampled_iou(solid, name, vh.read_dimensions(case)), 4)
    except Exception as error:
        row = {"name": name, "error": repr(error)}
    finally:
        agent.close()
    if "error" not in row:
        result_path.write_text(json.dumps(row))
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--all", action="store_true", help="every case of the eval set")
    parser.add_argument("--cases", type=int, default=8, help="cases per tier in the fixed sample (default 8)")
    parser.add_argument("--only", nargs="*", metavar="test_case_<n>")
    parser.add_argument("--iso", action="store_true", help="also make the isometric cut (off in the pipeline; slow)")
    parser.add_argument("--no-holes", action="store_true", help="skip step 2")
    parser.add_argument("--skip", nargs="*", default=[], metavar="STEP", help="later steps to leave out: rounds, depth, shading")
    parser.add_argument("--iou", action="store_true", help="measure the real overlap with the ground truth (slower)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    with open(EVAL / "index.csv", newline="") as f:
        tiers = {f"test_case_{row['n']}": row["tier"] for row in csv.DictReader(f)}
    names = args.only or (list(tiers) if args.all else sample(tiers, args.cases))
    options = {"iso": args.iso, "holes": not args.no_holes, "iou": args.iou, "skip": sorted(args.skip)}
    outline = os.environ.get("HULL_OUTLINE", "arcs")
    datums = os.environ.get("HULL_DATUMS", "0") == "1"
    hull_key = digest(SRC / "visual_hull.py", SRC / "outline_fit.py", SRC / "datums.py", SRC / "hull_agent.py",
                      SRC / "iso_support.py", SRC / "verify.py",
                      {"iso": options["iso"], "outline": outline, "datums": datums,
                       "trace": os.environ.get("HULL_TRACE", "centres"), "thin": os.environ.get("HULL_THIN_GUARD", "0"),
                        "thin_share": os.environ.get("HULL_THIN_SHARE", "0.01"), "tight": os.environ.get("HULL_TIGHT_SHARE", "0.90"),
                        "iso_excess": os.environ.get("HULL_ISO_EXCESS", "1.5"),
                        "iso_pieces": os.environ.get("HULL_ISO_PIECES", "1"),
                        "iso_gain": os.environ.get("HULL_ISO_MIN_GAIN", "0.01")})
    result_key = digest(*sorted(SRC.glob("*.py")), Path(__file__), options, {"outline": outline, "datums": datums,
                        "trace": os.environ.get("HULL_TRACE", "centres"), "thin": os.environ.get("HULL_THIN_GUARD", "0"),
                        "thin_share": os.environ.get("HULL_THIN_SHARE", "0.01"), "tight": os.environ.get("HULL_TIGHT_SHARE", "0.90"),
                        "iso_excess": os.environ.get("HULL_ISO_EXCESS", "1.5"),
                        "iso_pieces": os.environ.get("HULL_ISO_PIECES", "1"),
                        "iso_gain": os.environ.get("HULL_ISO_MIN_GAIN", "0.01")})
    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = [{"name": n, "options": options, "hull_key": hull_key, "result_key": result_key,
             "cache": not args.no_cache} for n in names]

    started = time.time()
    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(evaluate, jobs))
    elapsed = time.time() - started

    failed = [r for r in rows if "error" in r]
    rows = [r for r in rows if "error" not in r]
    steps = [s for s in STEPS if any(s in r["volumes"] for r in rows)]

    def scores(row: dict) -> list[float]:
        """Score after every step; a step that was skipped for this case keeps the previous score."""
        out, volume = [], None
        for step in steps:
            volume = row["volumes"].get(step, volume)
            out.append(min(volume, row["true"]) / max(volume, row["true"]))
        return out

    def mean(values) -> float:
        values = list(values)
        return sum(values) / len(values) if values else float("nan")

    header = f"{'':10}{'cases':>6}" + "".join(f"{s:>12}" for s in steps) + ("         IoU" if args.iou else "") + "       faces"
    print(header)
    for label in ("simple", "medium", "complex", "all"):
        group = [r for r in rows if label == "all" or tiers[r["name"]] == label]
        if not group:
            continue
        line = f"{label:10}{len(group):>6}" + "".join(f"{mean(scores(r)[i] for r in group):>12.4f}" for i in range(len(steps)))
        if args.iou:
            line += f"{mean(r['iou'] for r in group):>12.4f}"
        print(line + f"{mean(r['faces'] for r in group):>12.0f}")

    worst = sorted(rows, key=lambda r: scores(r)[-1])[:5]
    print("\nlowest:", ", ".join(f"{r['name'].removeprefix('test_case_')} ({scores(r)[-1]:.2f})" for r in worst))
    over = [r for r in rows if list(r["volumes"].values())[-1] < 0.985 * r["true"]]
    if over:
        print("cut below the true volume:", ", ".join(r["name"] for r in over))
    for row in failed:
        print(f"FAILED {row['name']}: {row['error']}")
    cached = sum(1 for r in rows if r.get("cached"))
    computed = [r["seconds"] for r in rows if not r.get("cached")]
    print(f"\n{len(rows)} cases in {elapsed:.0f} s ({cached} from cache"
          + (f", slowest computed {max(computed):.0f} s" if computed else "") + ")")

    summary = {"steps": steps, "mean": [mean(scores(r)[i] for r in rows) for i in range(len(steps))],
               "cases": sorted(r["name"] for r in rows), "options": options}
    last_path = CACHE / "last.json"
    if last_path.is_file():
        last = json.loads(last_path.read_text())
        if last.get("cases") == summary["cases"] and last.get("steps") == steps:
            change = summary["mean"][-1] - last["mean"][-1]
            print(f"final score {summary['mean'][-1]:.4f}, previous run {last['mean'][-1]:.4f} ({change:+.4f})")
    last_path.write_text(json.dumps(summary))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
