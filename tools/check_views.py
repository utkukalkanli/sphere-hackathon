"""Where a solid of ours disagrees with the views of its case, place by place. Needs no ground truth.

A whole-part number (volume, verifier score) can look right while the solid is wrong in two places
that cancel (decisions.md, Evaluation). This draws the solid from the 8 cameras with the tone table
and compares it with each view pixel by pixel:

- tone: part pixels, away from lines, whose tone is more than TONE_OFF levels from what the solid's
  face there would have (the surface points another way, or lies at another depth behind an edge);
- lines of the view that the solid has no edge near (geometry we lack), and edges of the solid the
  view does not draw (geometry we invented);
- silhouette pixels that differ.

    uv run python tools/check_views.py SOLIDS_DIR test_case_5 test_case_7 --save DIR
    uv run python tools/check_views.py SOLIDS_DIR test_case_38 --inputs datasets/eval/inputs

SOLIDS_DIR holds `test_case_<n>.stp` (for example the --output_dir of scripts/main.py). With --save,
one picture per view: our solid as the table shades it, beside the view with the tone-off pixels in
magenta, the lines we lack in red and our extra edges in blue.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("TRACE", "0")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from OCP.STEPControl import STEPControl_Reader  # noqa: E402

from agentic_cad import tone, verify  # noqa: E402

TONE_OFF = 15      # grey levels
NEAR_LINE_PX = 3   # tones this close to a line or an edge are not compared
BLOBS = 4          # the largest tone-off areas listed per view


def read_step(path: Path):
    reader = STEPControl_Reader()
    reader.ReadFile(str(path))
    reader.TransferRoots()
    return reader.OneShape()


def compare(solid, case: Path, save: Path | None) -> list[dict]:
    box = json.loads((case / "facts.json").read_text())["bounding_box_mm"]
    dims = {k: float(box[k]) for k in "xyz"}
    observed = verify.observe(case, dims)
    mesh = verify.tessellate(solid, 0.0005 * max(dims.values()), 0.15)
    drawings = {name: verify.draw(mesh, name, dims, view.mm_per_px) for name, view in observed.items()}
    table = tone.choose(observed, drawings) or tone.tables()["organisers"]
    kernel = np.ones((2 * NEAR_LINE_PX + 1, 2 * NEAR_LINE_PX + 1), np.uint8)
    rows = []
    for name, view in observed.items():
        drawing = drawings[name]
        seen = view.image.max(axis=2).astype(float)
        ours = np.where(drawing.mask, table.tone(tone.in_camera(drawing.normal, name)), 255.0)
        both = view.mask & drawing.mask
        near = cv2.dilate((view.lines | drawing.sharp).astype(np.uint8), kernel) > 0
        off = both & ~near & (np.abs(seen - ours) > TONE_OFF)
        off = cv2.morphologyEx(off.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
        lack = view.lines & view.mask & ~(cv2.dilate(drawing.sharp.astype(np.uint8), kernel) > 0)
        extra = drawing.sharp & ~(cv2.dilate(view.lines.astype(np.uint8), kernel) > 0)
        count, labels, stats, centres = cv2.connectedComponentsWithStats(off.astype(np.uint8), connectivity=8)
        order = np.argsort(-stats[1:, cv2.CC_STAT_AREA])[:BLOBS] + 1 if count > 1 else []
        blobs = [{"px": int(stats[i, cv2.CC_STAT_AREA]), "at": [int(centres[i][0]), int(centres[i][1])],
                  "view_tone": int(np.median(seen[labels == i])), "our_tone": int(np.median(ours[labels == i]))}
                 for i in order if stats[i, cv2.CC_STAT_AREA] >= 200]
        rows.append({"view": name, "part_px": int(both.sum()), "tone_off": int(off.sum()),
                     "lines": int((view.lines & view.mask).sum()), "lines_lacking": int(lack.sum()),
                     "edges": int(drawing.sharp.sum()), "edges_extra": int(extra.sum()),
                     "mask_diff": int((view.mask != drawing.mask).sum()), "blobs": blobs, "table": table.name})
        if save is not None:
            render = np.clip(ours, 0, 255).astype(np.uint8)
            render[drawing.sharp] = 30
            marked = view.image.copy()
            marked[off] = (0.4 * marked[off] + 0.6 * np.array([255, 0, 255])).astype(np.uint8)
            marked[cv2.dilate(lack.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0] = (0, 0, 255)
            marked[extra] = (255, 120, 0)
            save.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(save / f"{case.name}_{name}.png"), np.hstack([cv2.cvtColor(render, cv2.COLOR_GRAY2BGR), marked]))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("solids", type=Path, help="directory with test_case_<n>.stp")
    parser.add_argument("cases", nargs="+", help="case names, e.g. test_case_8")
    parser.add_argument("--inputs", type=Path, default=ROOT / "artifacts" / "inputs")
    parser.add_argument("--save", type=Path, help="write the marked pictures here")
    args = parser.parse_args()
    for name in args.cases:
        path = args.solids / f"{name}.stp"
        if not path.exists():
            print(f"{name}: no {path}")
            continue
        rows = compare(read_step(path), args.inputs / name, args.save)
        part = sum(r["part_px"] for r in rows) or 1
        print(f"{name} (tone table: {rows[0]['table'] if rows else '-'}): tone off on "
              f"{100 * sum(r['tone_off'] for r in rows) / part:.1f} % of the part pixels, "
              f"{100 * sum(r['lines_lacking'] for r in rows) / (sum(r['lines'] for r in rows) or 1):.0f} % of the views' lines lacking, "
              f"{100 * sum(r['edges_extra'] for r in rows) / (sum(r['edges'] for r in rows) or 1):.0f} % of our edges extra")
        for r in rows:
            print(f"  {r['view']:22s} tone off {100 * r['tone_off'] / (r['part_px'] or 1):5.1f} %   "
                  f"lines lacking {100 * r['lines_lacking'] / (r['lines'] or 1):3.0f} %   "
                  f"edges extra {100 * r['edges_extra'] / (r['edges'] or 1):3.0f} %   mask diff {r['mask_diff']} px")
            for blob in r["blobs"]:
                print(f"      {blob['px']:7d} px around {blob['at']}: view {blob['view_tone']}, ours {blob['our_tone']}")


if __name__ == "__main__":
    main()
