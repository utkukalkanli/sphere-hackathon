"""Grades the sources read off the views alone (src/agentic_cad/sources.py) against the ground truth.

    uv run python tools/check_sources.py                 # the 24 fixed eval parts
    uv run python tools/check_sources.py --practice --save DIR   # no truth: counts, and pictures to look at

Faces: the share of the true faces seen in a view that one area of ours covers (IoU 0.9), for the
areas between line pixels and for the faces of the drawing. Per isometric view: how much of the part gets an axis by tone and how often it is the right one;
for the faces given a level, how often it is within 3 px of the true plane, for all of them and
for the "sure" ones. Lines: the share of the true sharp edges the view's lines find, with and
without the tone steps.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
os.environ["TRACE"] = "0"
warnings.filterwarnings("ignore")

import cv2
import numpy as np

from agentic_cad import lines as between
from agentic_cad import drawing, regions, sources, tone, verify
from agentic_cad import visual_hull as vh

EVAL = ROOT / "datasets" / "eval"


def near(pixels: np.ndarray, target: np.ndarray) -> tuple[int, int]:
    distance = cv2.distanceTransform((~target).astype(np.uint8), cv2.DIST_L2, 3)
    return int((distance[pixels] <= verify.TOLERANCE_PX).sum()), int(pixels.sum())


def separated(labels: np.ndarray, face: np.ndarray, least: int = 150) -> list[tuple[int, float]]:
    """Per true face seen in a view (connected, at least `least` px): its area and its best overlap
    (IoU) with one area of `labels`."""
    result = []
    for value in np.unique(face[face >= 0]):
        count, parts, stats, _ = cv2.connectedComponentsWithStats((face == value).astype(np.uint8), connectivity=4)
        for index in range(1, count):
            area = int(stats[index, cv2.CC_STAT_AREA])
            if area < least:
                continue
            x, y, w, h = stats[index, :4]
            inside = parts[y:y + h, x:x + w] == index
            hit = np.bincount(labels[y:y + h, x:x + w][inside])
            hit[0] = 0
            best = int(hit.argmax())
            result.append((area, float(hit[best] / (area + (labels == best).sum() - hit[best])) if hit[best] else 0.0))
    return result


def grade(name: str) -> dict:
    from check_verify import ground_truth
    case = EVAL / "inputs" / name
    dims = dict(zip("xyz", vh.read_dimensions(case)))
    observed = verify.observe(case, dims)
    mesh = verify.tessellate(ground_truth(name), 0.3 * min(o.mm_per_px for o in observed.values()))
    truth = {view: verify.draw(mesh, view, dims, seen.mm_per_px) for view, seen in observed.items()}
    table = tone.choose(observed, truth)
    row = {"name": name, "lines": [0, 0, 0, 0], "with_steps": [0, 0, 0, 0], "part": 0, "axis": 0, "axis_right": 0, "faces": [],
           "separated": {"regions": [], "drawing": []}, "strokes": {}}
    for view, seen in observed.items():
        sketch = drawing.read(seen)
        for stroke in sketch.strokes:
            row["strokes"][stroke.kind] = row["strokes"].get(stroke.kind, 0) + 1
        old = regions.segment(seen)[0]
        free = old > 0
        _, nearest = cv2.distanceTransformWithLabels((~free).astype(np.uint8), cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
        lookup = np.zeros(int(nearest.max()) + 1, np.int32)
        lookup[nearest[free]] = old[free]
        for key, labels in (("regions", np.where(seen.mask, lookup[nearest], 0)), ("drawing", sketch.labels)):
            row["separated"][key] += separated(labels, truth[view].face)
    for view, seen in observed.items():
        inner = cv2.erode(seen.mask.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0  # the outline is always found
        for key, found in (("lines", seen.lines), ("with_steps", seen.lines | sources.steps(seen))):
            recall, precision = near(truth[view].sharp & inner, found), near(found & inner, truth[view].edges)
            row[key] = [a + b for a, b in zip(row[key], (*recall, *precision))]
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    for view, (labels, listed) in sources.faces(observed, dims, table).items():
        seen, drawn = observed[view], truth[view]
        row["part"] += int(seen.mask.sum())
        for face in listed:
            area = (labels == face.label) & drawn.mask
            normal = np.median(drawn.normal[area], axis=0) if area.any() else np.zeros(3)
            right = bool(normal[face.axis] * face.sign > 0.995)
            row["axis"] += face.area
            row["axis_right"] += face.area * right
            if right and face.level is not None:
                rows, cols = np.nonzero(area)
                points = between.lift(view, np.stack([cols, rows], axis=1).astype(float), drawn.depth[rows, cols], dims,
                                      seen.mm_per_px, seen.mask.shape[0])
                true_level = float(np.median(points[:, face.axis])) + centre[face.axis]
                row["faces"].append({"area": face.area, "sure": face.sure, "stands_out": face.on_lines >= sources.MIN_ON_LINES
                                     and face.on_lines - face.usual >= sources.MIN_STANDS_OUT,
                                     "off_px": abs(face.level - true_level) / observed[face.straight].mm_per_px})
    return row


def look(name: str, save: Path | None) -> None:
    case = ROOT / "artifacts" / "inputs" / name
    dims = dict(zip("xyz", vh.read_dimensions(case)))
    observed = verify.observe(case, dims)
    found = sources.faces(observed, dims, tone.tables().get(tone.DEFAULT))
    for view, (labels, listed) in found.items():
        part = int(observed[view].mask.sum())
        print(f"{name} {view}: {sum(f.area for f in listed) / part:.2f} of the part an axis face, "
              f"{sum(f.area for f in listed if f.sure) / part:.2f} with a sure level; "
              + ", ".join(f"{'xyz'[f.axis]}={f.level:.1f}" for f in listed if f.sure))


def save_pictures(name: str, save: Path) -> None:
    case = ROOT / "artifacts" / "inputs" / name
    dims = dict(zip("xyz", vh.read_dimensions(case)))
    save.mkdir(parents=True, exist_ok=True)
    for (view, kind), png in sources.pictures(verify.observe(case, dims), dims, tone.tables().get(tone.DEFAULT)).items():
        (save / f"{name}_{view}_{kind}.png").write_bytes(png)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--practice", action="store_true")
    parser.add_argument("--save", type=Path)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    if args.practice:
        for name in args.only or [f"test_case_{i}" for i in range(1, 10)]:
            look(name, args.save)
            if args.save:
                save_pictures(name, args.save)
        return
    names = args.only or json.loads((ROOT / "runs" / "_eval_cache" / "last.json").read_text())["cases"]
    with ProcessPoolExecutor(4) as pool:
        rows = list(pool.map(grade, names))
    for key in ("lines", "with_steps"):
        total = np.sum([row[key] for row in rows], axis=0)
        print(f"{key:10s} find {total[0] / total[1]:.3f} of the true sharp edges; {total[2] / total[3]:.3f} of their pixels lie on a true edge")
    for key, label in (("regions", "areas between line pixels (regions.py)"), ("drawing", "faces of the drawing (drawing.py)")):
        both = np.array([pair for row in rows for pair in row["separated"][key]])
        good = both[:, 1] >= 0.9
        print(f"true faces separated, {label}: {good.mean():.3f} of {len(both)} by count, {both[good, 0].sum() / both[:, 0].sum():.3f} by area; "
              f"merged or split (IoU < 0.5) {(both[:, 1] < 0.5).mean():.3f}")
    kinds = {}
    for row in rows:
        for kind, number in row["strokes"].items():
            kinds[kind] = kinds.get(kind, 0) + number
    print("strokes of the drawings:", kinds)
    part, axis, right = (sum(row[key] for row in rows) for key in ("part", "axis", "axis_right"))
    print(f"axis faces: {axis / part:.2f} of the isometric pixels, {right / axis:.3f} of them the right axis")
    faces = [face for row in rows for face in row["faces"]]
    for label, chosen in (("every level", faces), ("stands out", [f for f in faces if f["stands_out"]]), ("sure", [f for f in faces if f["sure"]])):
        area = sum(f["area"] for f in chosen)
        good = [f for f in chosen if f["off_px"] <= 3]
        print(f"levels, {label}: {len(chosen)} faces, {len(good)} within 3 px; by area {sum(f['area'] for f in good) / max(area, 1):.3f}")


if __name__ == "__main__":
    main()
