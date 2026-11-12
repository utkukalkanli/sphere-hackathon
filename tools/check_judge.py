"""Compares the two accept rules of the later steps (HULL_JUDGE=score and =local) against the ground truth.

    uv run python tools/check_judge.py                 # the 24 fixed eval parts without eval 99
    uv run python tools/check_judge.py --only test_case_38 test_case_236

For every part the solid after hull, holes, rounds and curved recesses is built once (cached in
runs/_judge_cache), and level faces, shading and pockets are then run on it under each rule. Every
cut a step tried is judged where it acts (docs/memory/decisions.md): points sampled in the bounding
box that the cut removed are looked up in the ground truth. A cut is "right" when the share of true
excess among them is above r / (1 + r), r being the volume of the solid it was cut from over the
part's: exactly then the overlap rises. The whole part's sampled IoU is printed beside it.

Eval 99 is left out because it does not give the same solid twice.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "datasets" / "eval"
SRC = ROOT / "src" / "agentic_cad"
CACHE = ROOT / "runs" / "_judge_cache"
POINTS = 20000
MIN_POINTS = 20   # a cut that removed fewer sampled points than this is too small to grade
STEPS = ("depth", "shading", "pockets")
BASE_SOURCES = ("visual_hull.py", "outline_fit.py", "hull_agent.py", "holes.py", "rounds.py", "curved.py",
                "verify.py", "iso_support.py", "export.py", "tone.py", "regions.py")

sys.path.insert(0, str(Path(__file__).parent))


def base_key() -> str:
    h = hashlib.sha1()
    for name in BASE_SOURCES:
        h.update((SRC / name).read_bytes())
    if (SRC / "spans.py").is_file():
        h.update((SRC / "spans.py").read_bytes())
    return h.hexdigest()[:12]


def evaluate(job: dict) -> dict:
    os.environ["TRACE"] = "0"
    import numpy as np
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    from agentic_cad import hull_agent, judge
    from agentic_cad import visual_hull as vh
    from check_verify import ground_truth

    name = job["name"]
    case = EVAL / "inputs" / name
    views = sorted((case / "images").glob("view_*.png"))
    dims = vh.read_dimensions(case)
    target = vh.read_volume(case)
    started = time.time()

    base_path = CACHE / f"base_{job['key']}_{name}.brep"
    if not base_path.is_file():
        agent = hull_agent.VisualHullAgent()
        try:
            solid = agent.carve(case.joinpath("description.md").read_text(), views)
            solid = agent.refine(solid, case, views, skip=("depth", "shading", "pockets", "split"))
        finally:
            agent.close()
        BRepTools.Write_s(solid, str(base_path))

    rng = np.random.default_rng(0)
    points = rng.uniform(0, 1, (POINTS, 3)) * np.array(dims)

    def inside(shape, which: np.ndarray) -> np.ndarray:
        """Of the points `which` (bool), those inside `shape`."""
        classifier = BRepClass3d_SolidClassifier(shape)
        result = np.zeros(POINTS, bool)
        for i in np.nonzero(which)[0]:
            classifier.Perform(gp_Pnt(*map(float, points[i])), 1e-6)
            result[i] = classifier.State() == TopAbs_IN
        return result

    everywhere = np.ones(POINTS, bool)
    truth = inside(ground_truth(name), everywhere)
    known: list[tuple] = []   # (shape, the points inside it)

    def points_in(shape, parent=None) -> np.ndarray:
        for other, value in known:
            if other is shape:
                return value
        # a cut only removes: the points of a candidate are among those of the solid it was cut from
        value = inside(shape, points_in(parent) if parent is not None else everywhere)
        known.append((shape, value))
        return value

    def iou(mask: np.ndarray) -> float:
        union = (mask | truth).sum()
        return float((mask & truth).sum() / union) if union else 0.0

    row = {"name": name, "rules": {}}
    for rule in job["rules"]:
        judge.RULE = rule
        solid = vh.largest_solid(vh.read_brep(str(base_path)))
        start = points_in(solid)
        row["iou_before"], row["volume_before"] = round(iou(start), 4), round(vh.volume(solid) / target, 4)
        agent = hull_agent.VisualHullAgent()
        agent.decisions = []
        clock = time.time()
        try:
            final = agent.refine(solid, case, views, skip=("rounds", "curved", "split"))
        finally:
            agent.close()
        cuts = []
        for decision in agent.decisions:
            before = points_in(decision["base"])
            after = points_in(decision["candidate"], decision["base"])
            removed = before & ~after
            count = int(removed.sum())
            ratio = vh.volume(decision["base"]) / target
            excess = float((removed & ~truth).sum() / count) if count else 0.0
            cuts.append({"step": decision["step"], "accepted": bool(decision["accepted"]), "points": count,
                         "removed_share_of_part": round((vh.volume(decision["base"]) - vh.volume(decision["candidate"])) / target, 4),
                         "excess_share": round(excess, 3), "needed": round(ratio / (1 + ratio), 3),
                         "right": None if count < MIN_POINTS else bool(excess > ratio / (1 + ratio)),
                         "real_material_share_of_part": round(float((removed & truth).sum()) / max(int(truth.sum()), 1), 4),
                         "verdict": decision["verdict"].summary() if decision["verdict"] is not None else None})
        end = points_in(final) if final is not solid else start
        row["rules"][rule] = {"iou": round(iou(end), 4), "volume": round(vh.volume(final) / target, 4),
                              "real_material_lost": round(float((start & truth & ~end).sum()) / max(int(truth.sum()), 1), 4),
                              "seconds": round(time.time() - clock, 1), "cuts": cuts}
    row["seconds"] = round(time.time() - started, 1)
    return row


def count(cuts: list[dict], accepted: bool, right) -> int:
    return sum(1 for c in cuts if c["accepted"] == accepted and c["right"] is right)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", nargs="*", metavar="test_case_<n>")
    parser.add_argument("--rules", nargs="*", default=["score", "local"], choices=["score", "local"])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--save", type=Path, help="write every cut of every part to this .json")
    args = parser.parse_args()

    from eval_hull import sample
    with open(EVAL / "index.csv", newline="") as f:
        tiers = {f"test_case_{row['n']}": row["tier"] for row in csv.DictReader(f)}
    names = args.only or [n for n in sample(tiers, 8) if n != "test_case_99"]
    CACHE.mkdir(parents=True, exist_ok=True)
    key = base_key()
    started = time.time()
    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(evaluate, [{"name": n, "key": key, "rules": args.rules} for n in names]))

    print(f"{'part':>6} {'tier':8} {'vol':>6} {'IoU':>7} | " + " | ".join(
        f"{rule:>10}: IoU   vol  kept right/wrong/small  refused right/wrong  lost%" for rule in args.rules))
    for row in rows:
        line = f"{row['name'].removeprefix('test_case_'):>6} {tiers[row['name']]:8} {row['volume_before']:6.3f} {row['iou_before']:7.4f} | "
        parts = []
        for rule in args.rules:
            r = row["rules"][rule]
            c = r["cuts"]
            parts.append(f"{'':10} {r['iou']:7.4f} {r['volume']:5.3f}  {count(c, True, True):10d}/{count(c, True, False)}/{count(c, True, None)}"
                         f"  {count(c, False, True):12d}/{count(c, False, False)}  {100 * r['real_material_lost']:6.2f}")
        print(line + " | ".join(parts))
    print()
    for rule in args.rules:
        every = [c for row in rows for c in row["rules"][rule]["cuts"]]
        ious = [row["rules"][rule]["iou"] for row in rows]
        print(f"{rule}: mean IoU {sum(ious) / len(ious):.4f} (before {sum(r['iou_before'] for r in rows) / len(rows):.4f}); "
              f"kept {count(every, True, True)} right, {count(every, True, False)} wrong, {count(every, True, None)} too small to grade; "
              f"refused {count(every, False, True)} right, {count(every, False, False)} wrong, {count(every, False, None)} too small; "
              f"{sum(row['rules'][rule]['seconds'] for row in rows):.0f} s in the three steps")
    if len(args.rules) > 1:
        a, b = args.rules[0], args.rules[-1]
        lower = [(row["name"], row["rules"][a]["iou"], row["rules"][b]["iou"]) for row in rows
                 if row["rules"][b]["iou"] < row["rules"][a]["iou"] - 0.01]
        print(f"parts more than 0.01 lower with {b} than with {a}: {lower or 'none'}")
    print(f"{len(rows)} parts in {time.time() - started:.0f} s")
    if args.save:
        args.save.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
