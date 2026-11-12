"""Deterministic: open cavities our solid fills, found where a view shows detail inside a face
that our solid has as one flat level.

A recess with a flat floor is one face of a view and recesses.py reads it. A cavity is more than
one face: seen from above, its walls, shoulders and bottom make a group of faces, some of them
curved or slanted, enclosed by the face it is sunk into (practice part 7: the open chamber of
the housing, 89 000 px of the top view). Our solid, carved from the silhouettes, has that whole
group as part of one flat face.

What stands on a face would show in a silhouette, and our solid contains the part: detail the
views draw inside a face that our solid has flat and level with it can only lie under that
level. So a group of faces that a flat facing face of a straight view encloses, over which our
solid is flat, square to the view and level with that face, is a cavity.

Its shape under the opening is not known. The column over the whole group is cut at a row of
depths and the local judge keeps the one at which the eight views fit best, if they fit strictly
better than before (propose.try_cuts): a flat-floored opening at the best depth, which the steps
after this one then work on.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import drawing, propose, trace, verify
from agentic_cad import visual_hull as vh

MIN_CAVITY_PX = 1000   # a smaller group of faces is not worth a search
LEVEL_PX = 2.0         # our solid is one level over the group, and level with the face around it, to this many pixels
FACING = 0.9995        # cosine: our solid's surface looks straight at the camera (as in depth.py)
MIN_SQUARE = 0.9       # share of our solid over the group that must do so
RING_PX = 5            # the face around a group is read this far out from it
MAX_CAVITIES = int(os.environ.get("HULL_MAX_CAVITIES", 4))   # per case, the largest first
MIN_EXCESS = 1.02      # nothing is looked for in a solid within this of the part's volume


def find(evidence, solid: TopoDS_Shape) -> list[dict]:
    """The groups of faces enclosed by a facing face of a straight view over which our solid is
    flat and level with that face, as recesses to search."""
    areas = evidence.areas()
    drawn = evidence.drawings(solid)
    listed = []
    for view in verify.ORTHO_VIEWS:
        if view not in evidence.observed:
            continue
        seen, labels, ours = evidence.observed[view], drawing.read(evidence.observed[view]).labels, drawn[view]
        kinds = {item.label: item.kind for item in areas[view][1]}
        square = (ours.normal @ verify.camera(view)[2]) >= FACING
        tolerance = LEVEL_PX * seen.mm_per_px
        ring = np.ones((2 * RING_PX + 1, 2 * RING_PX + 1), np.uint8)
        for face, kind in kinds.items():
            if kind != "facing":
                continue
            around = (labels == face).astype(np.uint8)
            contours, hierarchy = cv2.findContours(around, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
            if hierarchy is None:
                continue
            for contour, (_, _, _, parent) in zip(contours, hierarchy[0]):
                if parent < 0 or cv2.contourArea(contour) < MIN_CAVITY_PX:
                    continue   # the face's own outline, or a hole too small
                group = np.zeros(labels.shape, np.uint8)
                cv2.drawContours(group, [contour], -1, 1, cv2.FILLED)
                group = (group > 0) & (labels != face)
                if group.sum() < MIN_CAVITY_PX or (labels[group] == 0).any():
                    continue   # open to the background: a hole through the part, already there
                inside = np.unique(labels[group])
                if len(inside) == 1 and kinds.get(int(inside[0])) == "facing":
                    continue   # one flat face: a recess, read by recesses.py
                over = group & ours.mask
                if over.sum() < 0.98 * group.sum() or square[over].mean() < MIN_SQUARE:
                    continue   # our solid is not flat there
                surface = ours.depth[over]
                if np.percentile(surface, 90) - np.percentile(surface, 10) > tolerance:
                    continue   # our solid has something there already
                rim = (cv2.dilate(group.astype(np.uint8), ring) > 0) & (labels == face) & ours.mask
                if rim.sum() < 20 or abs(float(np.median(ours.depth[rim])) - float(np.median(surface))) > tolerance:
                    continue   # ... or stands at another level than the face around it
                listed.append({"type": "recess", "view": view, "area": group, "pixels": int(group.sum()), "faces": int(len(inside))})
    listed = sorted(listed, key=lambda entry: -entry["pixels"])[:MAX_CAVITIES]
    for number, entry in enumerate(listed, 1):
        entry["id"] = f"cavity_{entry['view']}_{number}"
    return listed


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step: search the depth of every cavity the views show inside a flat face of ours."""
    with trace.step("open_cavities", kind="code") as step:
        if case_dir is None:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        if target and vh.volume(solid) < MIN_EXCESS * target:
            step.log("the solid already has the part's volume; nothing to look for")
            return solid
        evidence = agent.evidence(case_dir, dims)
        pending = find(evidence, solid)
        step.metric(cavities=len(pending))
        if not pending:
            step.log("no view shows a group of faces inside a face that our solid has flat")
            return solid
        step.log("; ".join(f"{entry['view']}: {entry['faces']} faces over {entry['pixels']} px" for entry in pending))
        return propose.try_cuts(agent, step, solid, case_dir, dims, target, evidence, pending, label="cavities")
