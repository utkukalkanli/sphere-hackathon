"""Does src/agentic_cad/lines.py say rightly what the lines our solid does not reproduce are?

    uv run python tools/check_lines.py                      # the 24 fixed eval cases, against the ground truth
    uv run python tools/check_lines.py --only test_case_27  # chosen eval cases
    uv run python tools/check_lines.py --practice           # the 9 practice parts: counts only, no truth
    uv run python tools/check_lines.py --save DIR           # also write every view with its pieces drawn
    uv run python tools/check_lines.py -v                   # list every piece called "level" that is not

The solid is the one after holes, rounds, level faces and shading (not "curved", and not the two
steps that use these lines, pockets and split); it is cached in runs/_lines_cache, keyed by the code of those steps, so only the first run
is slow.

What a piece really is comes from the ground truth drawn from the same camera: under most of its
pixels the truth has a jump in depth ("step"), a crease, a boundary between faces that meet smoothly
or lie in one plane ("smooth"), or no boundary at all ("nothing"); and our surface is either where
the true one is or in front of it ("excess"). A piece is rightly called "level" when our surface is
right under it and the truth has a boundary there. In the pictures level is green, step red, below
violet, unseen blue; the face boundaries of our solid are grey.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
EVAL = ROOT / "datasets" / "eval"
CACHE = ROOT / "runs" / "_lines_cache"
SRC = ROOT / "src" / "agentic_cad"
STEP_FILES = ("visual_hull.py", "outline_fit.py", "holes.py", "rounds.py", "depth.py", "shading.py", "hull_agent.py")
VERDICTS = ("level", "step", "below", "unseen")
TRUTHS = ("step", "crease", "smooth", "nothing")
COLOURS = {"level": (0, 190, 0), "step": (0, 0, 255), "below": (255, 0, 200), "unseen": (255, 130, 0)}  # BGR


def solid_of(name: str, case: Path, practice: bool, key: str):
    """Our solid at the end of the pipeline, from the cache when the steps have not changed."""
    from OCP.BRepTools import BRepTools

    from agentic_cad import visual_hull as vh
    from agentic_cad.hull_agent import VisualHullAgent

    path = CACHE / f"{'practice' if practice else 'eval'}_{key}_{name}.brep"
    if path.is_file():
        return vh.largest_solid(vh.read_brep(str(path)))
    views = sorted((case / "images").glob("view_*.png"))
    agent = VisualHullAgent(use_iso=False, use_holes=True)
    try:
        solid = agent.build_hull(case.joinpath("description.md").read_text(), views)
        solid = agent.cut_holes(solid, case)
        solid = agent.refine(solid, case, views, skip=("curved", "pockets", "split"))
    finally:
        agent.close()
    solid = vh.tidy(solid)
    CACHE.mkdir(parents=True, exist_ok=True)
    BRepTools.Write_s(solid, str(path))
    return solid


def truth_under(real, mine, mm_per_px: float):
    """Per pixel: which kind of boundary the truth has nearby (index into TRUTHS), and whether our
    surface lies in front of the true one there."""
    import cv2

    kernel = np.ones((7, 7), np.uint8)
    depth = np.where(real.mask, real.depth, np.nan)
    high = cv2.dilate(np.nan_to_num(depth, nan=-1e9).astype(np.float32), kernel)
    low = cv2.erode(np.nan_to_num(depth, nan=1e9).astype(np.float32), kernel)
    jump = (high - low) > 3.0 * mm_per_px
    sharp = cv2.dilate(real.sharp.astype(np.uint8), kernel) > 0
    edge = cv2.dilate(real.edges.astype(np.uint8), kernel) > 0
    kind = np.where(sharp & jump, 0, np.where(sharp, 1, np.where(edge, 2, 3)))
    excess = mine.mask & real.mask & (np.where(mine.mask, mine.depth, 0) - low > 3.0 * mm_per_px)
    return kind, excess


def run(job: dict) -> dict:
    os.environ["TRACE"] = "0"
    import cv2

    import check_normals
    from agentic_cad import lines, shading, verify
    from agentic_cad import visual_hull as vh

    name, practice = job["name"], job["practice"]
    try:
        case = (ROOT / "artifacts" / "inputs" if practice else EVAL / "inputs") / name
        dims = dict(zip("xyz", vh.read_dimensions(case)))
        solid = solid_of(name, case, practice, job["key"])
        observed = verify.observe(case, dims)
        drawings = shading.Drawings(solid, observed, dims)
        found = lines.find(observed, drawings, dims)
        mesh = None
        if not practice:
            mesh = verify.tessellate(check_normals.truth(name), 0.3 * min(o.mm_per_px for o in observed.values()), 0.05)
        rows = []
        for view, pieces in found.items():
            seen = observed[view]
            if mesh is not None:
                real = verify.draw(mesh, view, dims, seen.mm_per_px, seen.mask.shape[0])
                kind, excess = truth_under(real, drawings[view], seen.mm_per_px)
            for piece in pieces:
                row = {"case": name, **piece.summary()}
                if mesh is not None:
                    cols, rows_ = np.rint(piece.points).astype(int).T
                    row["true"] = TRUTHS[int(np.bincount(kind[rows_, cols], minlength=4).argmax())]
                    row["excess"] = bool(excess[rows_, cols].mean() > 0.5)
                rows.append(row)
            if job["save"]:
                picture = seen.image.copy()
                picture[drawings[view].edges] = (190, 190, 190)
                for piece in pieces:
                    cv2.polylines(picture, [np.rint(piece.points).astype(np.int32)], False, COLOURS[piece.verdict], 2)
                Path(job["save"]).mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(Path(job["save"]) / f"{name}_{view}.png"), picture)
        return {"name": name, "rows": rows}
    except Exception as error:
        return {"name": name, "error": repr(error), "rows": []}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", nargs="*", metavar="test_case_<n>")
    parser.add_argument("--practice", action="store_true")
    parser.add_argument("--save", metavar="DIR")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    import eval_hull

    key = eval_hull.digest(*(SRC / f for f in STEP_FILES))
    if args.practice:
        names = args.only or sorted(p.name for p in (ROOT / "artifacts" / "inputs").iterdir() if p.is_dir())
    else:
        import csv
        with open(EVAL / "index.csv") as f:
            tiers = {f"test_case_{row['n']}": row["tier"] for row in csv.DictReader(f)}
        names = args.only or eval_hull.sample(tiers, 8)
    jobs = [{"name": n, "practice": args.practice, "save": args.save, "key": key} for n in names]
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(run, jobs))

    rows = [row for result in results for row in result["rows"]]
    for result in results:
        if "error" in result:
            print(f"{result['name']}: {result['error']}")
    print(f"{len(rows)} pieces on {len(results)} parts, {sum(r['length_px'] for r in rows):.0f} px")
    kinds = {k: sum(1 for r in rows if r["kind"] == k) for k in ("line", "arc", "circle")}
    print("  " + ", ".join(f"{count} {kind}s" for kind, count in kinds.items()))
    if args.practice:
        for verdict in VERDICTS:
            chosen = [r for r in rows if r["verdict"] == verdict]
            print(f"  {verdict:7} {len(chosen):5} pieces {sum(r['length_px'] for r in chosen):8.0f} px")
        return

    print(f"\nlength in px (pieces)   {'surface right':>32} | {'our surface in front (excess)':>32}")
    print(f"{'called':8}" + "".join(f"{t:>10}" for t in TRUTHS) + " |" + "".join(f"{t:>10}" for t in TRUTHS))
    for verdict in VERDICTS:
        cells = []
        for excess in (False, True):
            for true in TRUTHS:
                chosen = [r for r in rows if r["verdict"] == verdict and r["true"] == true and r["excess"] == excess]
                cells.append(f"{sum(r['length_px'] for r in chosen):6.0f}({len(chosen):3})")
            cells.append("|")
        print(f"{verdict:8}" + "".join(cells[:4]) + " |" + "".join(cells[5:9]))
    level = [r for r in rows if r["verdict"] == "level"]
    right = [r for r in level if not r["excess"] and r["true"] != "nothing"]
    splits = [r for r in rows if not r["excess"] and r["true"] in ("smooth", "crease")]
    found = [r for r in splits if r["verdict"] == "level"]
    total = lambda chosen: sum(r["length_px"] for r in chosen)
    if level:
        print(f"\ncalled level: {len(level)} pieces, {total(right) / max(total(level), 1):.1%} of their length rightly")
    if splits:
        print(f"true splits (surface right, smooth or crease): {len(splits)} pieces, "
              f"{total(found) / max(total(splits), 1):.1%} of their length called level")
    if args.verbose:
        for r in level:
            if r in right:
                continue
            print(f"  {r['case']} {r['view']} {r['kind']} {r['length_px']:.0f} px from {r['from']} true {r['true']}"
                  f"{' excess' if r['excess'] else ''} {({k: r[k] for k in ('seen', 'on_line', 'pairs', 'same_here', 'same_iso') if k in r})}")


if __name__ == "__main__":
    main()
