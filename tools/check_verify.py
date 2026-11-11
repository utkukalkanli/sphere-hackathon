"""How well does the verifier (src/agentic_cad/verify.py) tell a good solid from a worse one?

    uv run python tools/check_verify.py                 # the 24 fixed eval cases
    uv run python tools/check_verify.py --only test_case_5 --images <dir>   # also save pictures

For every case it scores the ground truth, our pipeline's solid and the plain bounding box, and
prints the verifier's numbers next to the real overlap with the ground truth.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
EVAL = ROOT / "datasets" / "eval"


def ground_truth(name: str):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    reader.ReadFile(str(EVAL / "ground_truth" / f"{name}.stp"))
    reader.TransferRoots()
    truth = reader.OneShape()
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(truth, box, False, False)
    low = box.CornerMin()
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(-low.X(), -low.Y(), -low.Z()))
    return BRepBuilderAPI_Transform(truth, move, True).Shape()


def overlay(observed, drawing):
    """Picture: the view's lines in black, the candidate's sharp edges in red, both in green."""
    import numpy as np
    image = np.full((*observed.mask.shape, 3), 255, np.uint8)
    image[observed.mask] = (225, 225, 225)
    image[observed.lines] = (0, 0, 0)
    image[drawing.edges & ~observed.lines] = (255, 180, 120)
    image[drawing.sharp & ~observed.lines] = (0, 0, 255)
    image[drawing.sharp & observed.lines] = (0, 160, 0)
    return image


def evaluate(job: dict) -> dict:
    os.environ["TRACE"] = "0"
    import cv2
    from agentic_cad import verify
    from agentic_cad import visual_hull as vh
    from agentic_cad.hull_agent import VisualHullAgent
    from eval_hull import sampled_iou

    name = job["name"]
    case = EVAL / "inputs" / name
    dims = dict(zip("xyz", vh.read_dimensions(case)))
    observed = verify.observe(case, dims)
    agent = VisualHullAgent()
    try:
        views = sorted((case / "images").glob("view_*.png"))
        ours = agent.cut_holes(agent.build_hull("", views), case)
    finally:
        agent.close()
    row = {"name": name, "views": len(observed)}
    for label, solid in (("truth", ground_truth(name)), ("ours", ours), ("box", vh.box(dims))):
        started = time.time()
        report = verify.check(solid, case, observed)
        row[label] = report.as_dict() | {"seconds": round(time.time() - started, 2)}
        row[label]["iou"] = 1.0 if label == "truth" else round(sampled_iou(solid, name, tuple(dims.values())), 4)
        if job["images"]:
            mesh = verify.tessellate(solid, 0.3 * min(o.mm_per_px for o in observed.values()))
            for view, seen in observed.items():
                drawing = verify.draw(mesh, view, dims, seen.mm_per_px)
                cv2.imwrite(str(Path(job["images"]) / f"{name}_{label}_{view}.png"), overlay(seen, drawing))
    return row


def main() -> None:
    from eval_hull import sample
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", nargs="*", metavar="test_case_<n>")
    parser.add_argument("--images", type=Path, help="save an overlay picture of every view here")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--json", type=Path, help="write all numbers here")
    args = parser.parse_args()
    with open(EVAL / "index.csv", newline="") as f:
        tiers = {f"test_case_{row['n']}": row["tier"] for row in csv.DictReader(f)}
    names = args.only or sample(tiers, 8)
    if args.images:
        args.images.mkdir(parents=True, exist_ok=True)
    jobs = [{"name": n, "images": str(args.images) if args.images else None} for n in names]
    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(evaluate, jobs))
    print(f"{'case':>6} {'tier':8} {'solid':6} {'iou':>6} {'score':>6} {'sil':>6} {'prec':>6} {'rec':>6} {'vol':>6} {'area':>6} {'s':>5}")
    for row in rows:
        for label in ("truth", "ours", "box"):
            r = row[label]
            print(f"{row['name'].removeprefix('test_case_'):>6} {tiers[row['name']]:8} {label:6} {r['iou']:6.3f} {r['score']:6.3f} "
                  f"{r['silhouette']:6.3f} {r['precision']:6.3f} {r['recall']:6.3f} {r['volume']:6.2f} {r['area']:6.2f} {r['seconds']:5.1f}")
    ranked = sum(1 for r in rows if r["truth"]["score"] > r["ours"]["score"] > r["box"]["score"])
    print(f"\ntruth > ours > box by score: {ranked} of {len(rows)} cases")
    for label in ("truth", "ours", "box"):
        print(f"{label:6} mean score {sum(r[label]['score'] for r in rows) / len(rows):.3f}, "
              f"mean iou {sum(r[label]['iou'] for r in rows) / len(rows):.3f}")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
