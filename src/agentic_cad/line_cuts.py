"""Step 3: cuts read from the lines inside the views (pockets, steps, recesses open to one side).

The silhouettes say where the part is not; the dark lines inside a render say where its faces end.
A region of a straight view enclosed by lines is (mostly) one face seen head-on. If our solid has
no line where the render draws that region's boundary, the face lies deeper than our solid: there
is material to remove in front of it. How deep is read from the other views, the way a draughtsman
reads a drawing: a face perpendicular to the view direction shows up in the side views as a line at
its depth. So each such region is tried as a pocket from the viewer's side down to each depth the
side views offer, and a try is kept only if

  - the solid keeps at least MIN_VOLUME_SHARE of the true volume (`facts.json`),
  - no view shows new background where the render shows the part (`views.ViewSet.missing`), and
  - the line drawings of all eight views, both isometric ones included, agree better with the
    renders than before (`edges.LineViews.mismatch`). The isometric views are what tell a right
    depth from a wrong one: their lines show where a pocket's walls and floor really meet.

Deterministic, no model. Budget-limited: the most promising regions are tried first.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import edges
from agentic_cad import visual_hull as vh
from agentic_cad.views import ORTHO_VIEWS

MIN_REGION_PX = 150        # regions smaller than this are line noise
MIN_MISSING_SHARE = 0.15   # share of a region's boundary the render draws and our solid does not
MIN_MISSING_PX = 25
PEAK_SHARE = 0.04          # a depth line must run along at least this share of a side view
MAX_DEPTHS = 8             # depths tried per region, nearest the viewer first
# Tries per case. This, not the time budget, is what normally ends the step, so the result does not
# depend on how fast the machine is; the time budget only stops a step that runs far too long.
MAX_TRIES = int(os.environ.get("LINE_MAX_TRIES", 30))
MIN_GAIN = 0.01            # a cut must remove at least this share of the line mismatch ...
MIN_GAIN_PX = 40           # ... and at least this many pixels
MARGIN = 0.02              # pockets start this share of the box outside it, so no face coincides
MIN_VOLUME_SHARE = 1.0     # unlike holes, a pocket may not take the solid below the true volume at all
ISO_VIEWS = ("iso_front_right_top", "iso_back_left_bottom")


def cut_away(shape: TopoDS_Shape, silhouette: vh.Silhouette) -> TopoDS_Shape | None:
    """Remove the silhouette's prism from `shape`. One valid solid, or None. For `Cutter.run`."""
    operation = BRepAlgoAPI_Cut(shape, vh.prism(silhouette))
    if not operation.IsDone():
        return None
    solid = vh.largest_solid(operation.Shape())
    if solid is None or vh.volume(solid) <= 0 or not BRepCheck_Analyzer(solid).IsValid():
        return None
    return solid


def _axis(vector: np.ndarray) -> int:
    return int(np.argmax(np.abs(vector)))


def depth_candidates(view: str, line_views: edges.LineViews, dims: np.ndarray) -> list[float]:
    """Depths (mm from the viewer's side of the box) at which the side views draw a line across:
    the depths of faces perpendicular to this view's direction."""
    direction = np.array(edges.ORTHO_DIRECTIONS[view], float)
    axis = _axis(direction)
    values = []
    for other in line_views.views:
        if other.name not in ORTHO_VIEWS or other.name == view:
            continue
        (u, flip_u), (v, flip_v) = ORTHO_VIEWS[other.name]
        lines = other.lines
        height, width = lines.shape
        if u == axis:    # lines across the axis are vertical in this image: count per column
            counts, size, flip = lines.sum(axis=0), width, flip_u
            needed = PEAK_SHARE * height
        elif v == axis:  # horizontal: count per row (rows run top-down)
            counts, size, flip = lines.sum(axis=1)[::-1], height, flip_v
            needed = PEAK_SHARE * width
        else:
            continue
        for index in np.where(counts >= needed)[0]:
            coordinate = index / (size - 1) * dims[axis]
            values.append(dims[axis] - coordinate if flip else coordinate)
    depths = sorted({round(d if direction[axis] > 0 else dims[axis] - d, 3) for d in values})
    merged = []
    for depth in depths:  # a line is 1-2 px wide: keep one value per line
        if 0.01 * dims[axis] < depth < 0.99 * dims[axis] and (not merged or depth - merged[-1] > 0.006 * dims[axis]):
            merged.append(depth)
    return merged


def regions(view: edges.LineView, part: np.ndarray, missing: np.ndarray) -> list[tuple[int, np.ndarray]]:
    """Regions of a straight view enclosed by render lines whose boundary our solid does not draw,
    with the number of such boundary pixels, most first."""
    free = part & ~cv2.dilate(view.lines.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(free.astype(np.uint8), connectivity=4)
    found = []
    grow = np.ones((5, 5), np.uint8)
    # the silhouette's own outline is right already (step 1): judge a region by its inner boundary
    inner = cv2.erode(part.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < MIN_REGION_PX:
            continue
        region = labels == label
        grown = cv2.dilate(region.astype(np.uint8), grow).astype(bool) & part
        boundary = grown & ~region & inner
        unexplained = int((boundary & missing).sum())
        if unexplained >= max(MIN_MISSING_PX, MIN_MISSING_SHARE * boundary.sum()):
            found.append((unexplained, grown))
    return sorted(found, key=lambda item: -item[0])


def region_silhouette(view: str, region: np.ndarray, dims: np.ndarray, known: dict | None) -> vh.Silhouette | None:
    """The region as a silhouette on the viewer's side of the box, to be extruded into it."""
    right, up = edges.ortho_basis(view)
    direction = np.array(edges.ORTHO_DIRECTIONS[view], float)
    height, width = region.shape
    du, dv = dims[_axis(right)], dims[_axis(up)]
    px = (du / (width - 1) + dv / (height - 1)) / 2
    mask = region.astype(np.uint8) * 255
    outlines = vh.trace_outlines(mask, lambda p: np.column_stack(
        [p[:, 0] / (width - 1) * du, (height - 1 - p[:, 1]) / (height - 1) * dv]), px, known)
    if not outlines:
        return None
    origin = np.zeros(3)
    for vector, size in ((right, du), (up, dv)):
        if vector.sum() < 0:  # the image axis runs against the coordinate axis: (0, 0) sits at its far end
            origin[_axis(vector)] = size
    axis = _axis(direction)
    margin = MARGIN * float(max(dims))
    origin[axis] = -margin if direction[axis] > 0 else dims[axis] + margin
    return vh.Silhouette(name=f"lines_{view}", views=[view], mask=mask, outlines=outlines, origin=origin,
                         right=right, up=up, direction=direction, depth=margin)


def total(mismatch: dict[str, tuple[int, int]], views=None) -> int:
    return sum(a + b for name, (a, b) in mismatch.items() if views is None or name in views)


def carve(solid, views: list[Path], dims: tuple[float, float, float], target: float | None, cutter,
          view_set, known_for, step, budget: float, cut_timeout: float, min_volume_share: float = MIN_VOLUME_SHARE):
    """Try the regions of every straight view as pockets; return the solid with the cuts that help."""
    deadline = time.monotonic() + budget
    dims = np.array(dims, float)
    line_views = edges.LineViews(views, dims, edges.cameras_for(view_set))
    mismatch = line_views.mismatch(solid)
    current, current_iso = total(mismatch), total(mismatch, ISO_VIEWS)
    step.log(f"line mismatch before: {current} px " + ", ".join(f"{k} {a}+{b}" for k, (a, b) in mismatch.items()))
    missing_now = view_set.missing(solid)

    grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * edges.TOLERANCE_PX + 1,) * 2)
    drawn = line_views.drawn(solid)
    candidates = []
    for view in line_views.views:
        if view.name not in ORTHO_VIEWS:
            continue
        part = vh.part_mask(vh.find_view(views, view.name))
        if part is None or part.shape != view.lines.shape:
            continue
        missing = view.lines & ~cv2.dilate(drawn[view.name].astype(np.uint8), grow).astype(bool)
        depths = depth_candidates(view.name, line_views, dims)
        for unexplained, region in regions(view, part, missing):
            candidates.append((unexplained, view.name, region, depths))
    candidates.sort(key=lambda item: -item[0])
    step.log(f"{len(candidates)} region(s) with lines our solid lacks")

    tried = kept = 0
    for unexplained, view, region, depths in candidates:
        if time.monotonic() > deadline:
            step.log("out of time: the result now depends on the machine's speed", level="warning")
            break
        if tried >= MAX_TRIES:
            break
        right, up = edges.ortho_basis(view)
        # hole hints are given for unflipped image axes only
        known = known_for(_axis(right), _axis(up)) if right.sum() > 0 and up.sum() > 0 else None
        silhouette = region_silhouette(view, region, dims, known)
        if silhouette is None:
            continue
        best = None
        for depth in depths[:MAX_DEPTHS]:
            if time.monotonic() > deadline or tried >= MAX_TRIES:
                break
            tried += 1
            try:
                carved = cutter.run(cut_away, solid, replace(silhouette, depth=silhouette.depth + depth), cut_timeout)
            except Exception:
                carved = None
            if carved is None:
                continue
            after = vh.volume(carved)
            if after >= vh.volume(solid) - 1e-6:  # removed nothing
                continue
            if target and after < min_volume_share * target:
                break  # deeper only removes more
            now = view_set.missing(carved)
            if any(now[v] - missing_now[v] > view_set.tolerance(v) for v in now):
                break  # opened background where the part is; deeper only opens more
            try:
                result = line_views.mismatch(carved)
            except Exception as error:  # the line drawing failed for this candidate: skip it
                step.log(f"{view}: could not draw the lines at {depth:.2f} mm ({error!r})", level="warning")
                continue
            score, iso = total(result), total(result, ISO_VIEWS)
            # the isometric views must agree too: they show where a pocket's walls and floor meet
            if iso >= current_iso:
                continue
            if best is None or score < best[0]:
                best = (score, iso, depth, carved, now)
        if best is not None and best[0] < current - max(MIN_GAIN_PX, MIN_GAIN * current):
            score, iso, depth, carved, now = best
            rows, cols = np.where(region)
            h, w = region.shape
            span = (f"image x {cols.min() / (w - 1):.2f}-{cols.max() / (w - 1):.2f}, "
                    f"y {rows.min() / (h - 1):.2f}-{rows.max() / (h - 1):.2f} of the view")
            step.log(f"{view}: pocket {depth:.2f} mm deep at {span} ({unexplained} px of lines unexplained): "
                     f"mismatch {current} -> {score} px (iso {current_iso} -> {iso}), "
                     f"volume {vh.volume(solid):.0f} -> {vh.volume(carved):.0f} mm3")
            solid, current, current_iso, missing_now = carved, score, iso, now
            kept += 1
    step.metric(regions=len(candidates), tried=tried, kept=kept, line_mismatch=current)
    return solid, kept
