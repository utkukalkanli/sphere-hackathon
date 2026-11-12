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

Shading says what kind of face a region is. A flat face is one even tone; a cylinder seen side-on
darkens gradually across its axis and keeps its tone along it. So a region whose tone changes along
one image direction only is tried as a cylinder instead of a flat-bottomed pocket: concave (a
recess, the cylinder is cut out of the region) or convex (a shaft or boss: the region's material in
front of the cylinder's axis and outside the cylinder is cut, which rounds the square sections the
silhouettes leave). Diameters come from facts.json's cylinder lists and from the region's width.

Depths are matched to regions the way a drawing is read: a face of a region lies at a depth where
the side views draw a line *across that region's own extent* (its x range in the front view, its y
range in the side view), not just anywhere. A circle inside a straight view's outline is the top of
a cylinder (a boss, a turned end) standing on a face below it: the silhouettes leave it as a square
block, so the block's corners outside the circle are cut, down to the base the side views show.

Deterministic, no model. A fixed number of tries per case: the most promising regions first.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import edges, revolve
from agentic_cad import visual_hull as vh
from agentic_cad.views import ORTHO_VIEWS

MIN_REGION_PX = 150        # regions smaller than this are line noise
MIN_MISSING_SHARE = 0.15   # share of a region's boundary the render draws and our solid does not
MIN_MISSING_PX = 25
PEAK_SHARE = 0.04          # a depth line must run along at least this share of a side view
MAX_DEPTHS = 8             # depths tried per region, nearest the viewer first
# Tries per case. This, not the time budget, is what normally ends the step, so the result does not
# depend on how fast the machine is; the time budget only stops a step that runs far too long.
MAX_TRIES = int(os.environ.get("LINE_MAX_TRIES", 60))
CURVED_STD = 4.0           # a region whose tone varies more than this (grey levels) is not flat ...
CURVED_SHARE = 0.85        # ... and is a cylinder if its tone along one image direction explains this much
MAX_DIAMETERS = 2          # diameters tried per curved region and kind
MAX_PER_REGION = 10        # tries per region, so one region cannot use up the case's tries
REGION_LINE_SHARE = 0.3    # a region's depth line must run along this share of the region's extent
CIRCLE_ROUNDNESS = 0.85    # 4 pi area / perimeter^2 of a region that counts as a circle
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


def _valid(shape) -> TopoDS_Shape | None:
    solid = vh.largest_solid(shape)
    if solid is None or vh.volume(solid) <= 0 or not BRepCheck_Analyzer(solid).IsValid():
        return None
    return solid


def cut_curved(shape: TopoDS_Shape, spec: dict) -> TopoDS_Shape | None:
    """Cut a cylinder-shaped piece out of `shape` within a region's prism. For `Cutter.run`.
    concave: the cylinder itself (inside the prism) is removed; convex: the prism outside the cylinder."""
    prism = vh.prism(spec["silhouette"])
    frame = gp_Ax2(gp_Pnt(*map(float, spec["point"])), gp_Dir(*map(float, spec["axis"])))
    cylinder = BRepPrimAPI_MakeCylinder(frame, float(spec["radius"]), float(spec["length"])).Shape()
    if spec["kind"] == "concave":
        tool = BRepAlgoAPI_Common(prism, cylinder).Shape()
    else:
        tool = BRepAlgoAPI_Cut(prism, cylinder).Shape()
    operation = BRepAlgoAPI_Cut(shape, tool)
    return _valid(operation.Shape()) if operation.IsDone() else None


def curvature(shading: np.ndarray | None, region: np.ndarray) -> str | None:
    """'u' or 'v' if the region's tone is constant along that image direction and varies across it
    (a cylinder with its axis that way), None for an even (flat) or irregular region."""
    if shading is None:
        return None
    rows, cols = np.where(region)
    tone = shading[rows, cols]
    spread = tone.var()
    if len(tone) < MIN_REGION_PX or np.sqrt(spread) < CURVED_STD:
        return None
    shares = {}
    for name, across in (("u", rows), ("v", cols)):  # constant along u: the tone depends on the row only
        _, bins = np.unique(across // 4, return_inverse=True)
        means = np.bincount(bins, tone) / np.bincount(bins)
        shares[name] = 1 - ((tone - means[bins]) ** 2).mean() / spread
    name = max(shares, key=shares.get)
    return name if shares[name] >= CURVED_SHARE else None


def cut_boss(shape: TopoDS_Shape, spec: dict) -> TopoDS_Shape | None:
    """Round a square block into a cylinder: cut the box around the circle minus the cylinder,
    from the viewer's side down to the base depth. For `Cutter.run`."""
    low, high = np.asarray(spec["low"], float), np.asarray(spec["high"], float)
    block = BRepPrimAPI_MakeBox(gp_Pnt(*map(float, low)), gp_Pnt(*map(float, high))).Shape()
    frame = gp_Ax2(gp_Pnt(*map(float, spec["point"])), gp_Dir(*map(float, spec["axis"])))
    cylinder = BRepPrimAPI_MakeCylinder(frame, float(spec["radius"]), float(spec["length"])).Shape()
    tool = BRepAlgoAPI_Cut(block, cylinder).Shape()
    operation = BRepAlgoAPI_Cut(shape, tool)
    return _valid(operation.Shape()) if operation.IsDone() else None


def circle_of(region: np.ndarray) -> tuple[float, float, float] | None:
    """(col, row, radius) in pixels if the region's outer outline is a circle."""
    contours, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area, perimeter = cv2.contourArea(contour), cv2.arcLength(contour, True)
    if area < MIN_REGION_PX or perimeter <= 0 or 4 * np.pi * area / perimeter ** 2 < CIRCLE_ROUNDNESS:
        return None
    (col, row), radius = cv2.minEnclosingCircle(contour)
    if area < 0.85 * np.pi * radius ** 2:
        return None
    return col, row, radius


def extent(view: str, region: np.ndarray, dims: np.ndarray) -> dict[int, tuple[float, float]]:
    """The region's range in true coordinates along the two image axes of a straight view."""
    (u, flip_u), (v, flip_v) = ORTHO_VIEWS[view]
    height, width = region.shape
    rows, cols = np.where(region)
    a = np.array([cols.min(), cols.max()]) / (width - 1) * dims[u]
    b = (height - 1 - np.array([rows.max(), rows.min()])) / (height - 1) * dims[v]
    a = np.sort(dims[u] - a) if flip_u else a
    b = np.sort(dims[v] - b) if flip_v else b
    return {u: (float(a[0]), float(a[1])), v: (float(b[0]), float(b[1]))}


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


def region_depths(view: str, region: np.ndarray, line_views: edges.LineViews, dims: np.ndarray) -> list[float]:
    """Depths at which a side view draws a line across this region's own extent: the depths its
    faces can have. Nearest the viewer first."""
    direction = np.array(edges.ORTHO_DIRECTIONS[view], float)
    axis = _axis(direction)
    ranges = extent(view, region, dims)
    values = []
    for other in line_views.views:
        if other.name not in ORTHO_VIEWS or other.name == view:
            continue
        (u, flip_u), (v, flip_v) = ORTHO_VIEWS[other.name]
        lines = other.lines
        height, width = lines.shape
        if u == axis and v in ranges:      # depth runs along the columns, the shared axis down the rows
            low, high = ranges[v]
            r = (height - 1) * (1 - np.array([high, low]) / dims[v]) if not flip_v else (height - 1) * np.array([low, high]) / dims[v]
            r0, r1 = int(max(0, np.floor(r.min()))), int(min(height - 1, np.ceil(r.max())))
            counts, size, flip, span = lines[r0:r1 + 1].sum(axis=0), width, flip_u, r1 - r0 + 1
        elif v == axis and u in ranges:    # depth runs down the rows, the shared axis along the columns
            low, high = ranges[u]
            c = (width - 1) * np.array([low, high]) / dims[u] if not flip_u else (width - 1) * (1 - np.array([high, low]) / dims[u])
            c0, c1 = int(max(0, np.floor(c.min()))), int(min(width - 1, np.ceil(c.max())))
            counts, size, flip, span = lines[:, c0:c1 + 1].sum(axis=1)[::-1], height, flip_v, c1 - c0 + 1
        else:
            continue
        for index in np.where(counts >= max(3, REGION_LINE_SHARE * span))[0]:
            coordinate = index / (size - 1) * dims[axis]
            values.append(dims[axis] - coordinate if flip else coordinate)
    depths = sorted({round(d if direction[axis] > 0 else dims[axis] - d, 3) for d in values})
    merged = []
    for depth in depths:
        if 0.01 * dims[axis] < depth < 0.99 * dims[axis] and (not merged or depth - merged[-1] > 0.006 * dims[axis]):
            merged.append(depth)
    return merged


def boss_options(view: str, region: np.ndarray, circle: tuple[float, float, float], dims: np.ndarray,
                 depths: list[float], views: list[Path] | None = None) -> list[tuple[str, dict, object]]:
    """For a circle in a straight view: a cylinder standing towards the viewer, base at each depth."""
    (u, flip_u), (v, flip_v) = ORTHO_VIEWS[view]
    height, width = region.shape
    col, row, radius_px = circle
    cu = col / (width - 1) * dims[u]
    cv = (height - 1 - row) / (height - 1) * dims[v]
    cu, cv = (dims[u] - cu if flip_u else cu), (dims[v] - cv if flip_v else cv)
    radius = radius_px * (dims[u] / (width - 1) + dims[v] / (height - 1)) / 2
    direction = np.array(edges.ORTHO_DIRECTIONS[view], float)
    axis = _axis(direction)
    margin = MARGIN * float(max(dims))
    options = []
    for depth in depths[:MAX_DEPTHS]:
        low, high = np.zeros(3), np.zeros(3)
        low[u], high[u] = cu - radius * 1.05, cu + radius * 1.05
        low[v], high[v] = cv - radius * 1.05, cv + radius * 1.05
        if direction[axis] > 0:   # the viewer is at coordinate 0 of this axis
            low[axis], high[axis] = -margin, depth
        else:
            low[axis], high[axis] = dims[axis] - depth, dims[axis] + margin
        point = np.zeros(3)
        point[u], point[v], point[axis] = cu, cv, -margin
        cylinder_axis = np.zeros(3)
        cylinder_axis[axis] = 1.0
        face = 0.0 if direction[axis] > 0 else float(dims[axis])
        base = depth if direction[axis] > 0 else float(dims[axis]) - depth
        turned = revolve.boss_option(views, dims, axis, {u: cu, v: cv}, radius, face, base) if views else None
        if turned is not None:  # shaped by the side views: a cone or a dome as well as a cylinder
            options.append((turned[0], turned[1], revolve.cut_revolved))
        options.append((f"boss d {2 * radius:.2f} at ({cu:.1f}, {cv:.1f}), base {depth:.2f} mm deep", {
            "low": low, "high": high, "point": point, "axis": cylinder_axis, "radius": radius,
            "length": dims[axis] + 2 * margin}, cut_boss))
    return options


def regions(view: edges.LineView, part: np.ndarray, missing: np.ndarray) -> list[tuple[int, str | None, np.ndarray]]:
    """Regions of a straight view enclosed by render lines that are worth trying: their boundary has
    lines our solid does not draw, or their shading shows a cylinder. With the number of such
    boundary pixels and the cylinder axis ('u', 'v' or None), cylinders first, then most unexplained."""
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
        curved = curvature(view.shading, cv2.erode(region.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
        if curved or unexplained >= max(MIN_MISSING_PX, MIN_MISSING_SHARE * boundary.sum()):
            found.append((unexplained, curved, grown))
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


def curved_options(view: str, region: np.ndarray, silhouette: vh.Silhouette, axis_name: str, dims: np.ndarray,
                   depths: list[float], diameters: dict) -> list[tuple[str, dict]]:
    """Cylinder cuts for a region whose shading shows a cylinder with its axis along image `axis_name`."""
    right, up = edges.ortho_basis(view)
    direction = np.array(edges.ORTHO_DIRECTIONS[view], float)
    height, width = region.shape
    du, dv = dims[_axis(right)], dims[_axis(up)]
    rows, cols = np.where(region)
    if axis_name == "u":  # axis along the image's horizontal: the width is measured down the rows
        axis, across = right, up
        low, high = (height - 1 - rows.max()) / (height - 1) * dv, (height - 1 - rows.min()) / (height - 1) * dv
    else:
        axis, across = up, right
        low, high = cols.min() / (width - 1) * du, cols.max() / (width - 1) * du
    span, centre = high - low, (high + low) / 2
    face = silhouette.origin + direction * MARGIN * float(max(dims))  # (0, 0) on the viewer's face of the box
    length = 3 * float(max(dims))
    options = []
    for kind in ("convex", "concave"):
        listed = sorted(diameters.get(kind, []), key=lambda d: abs(d - span))
        if kind == "convex":
            listed = [d for d in listed if abs(d - span) <= 0.08 * span]
        else:
            listed = [d for d in listed if 0.95 * span <= d <= 4 * span]
        for diameter in (listed + [span])[:MAX_DIAMETERS]:
            radius = diameter / 2
            for edge in [0.0] + depths[:1]:  # depth of the nearest point (convex) or of the recess edges
                if kind == "convex":
                    centre_depth = edge + radius
                    prism_depth = centre_depth
                else:
                    centre_depth = edge - np.sqrt(max(radius ** 2 - (span / 2) ** 2, 0.0))
                    prism_depth = float(dims[_axis(direction)])
                point = face + across * centre + direction * centre_depth - axis * float(max(dims))
                options.append((f"{kind} cylinder d {diameter:.2f}, nearest at {edge:.2f} mm", {
                    "kind": kind, "point": point, "axis": axis, "radius": radius, "length": length,
                    "silhouette": replace(silhouette, depth=silhouette.depth + prism_depth)}))
    return options


def total(mismatch: dict[str, tuple[int, int]], views=None) -> int:
    return sum(a + b for name, (a, b) in mismatch.items() if views is None or name in views)


def carve(solid, views: list[Path], dims: tuple[float, float, float], target: float | None, cutter,
          view_set, known_for, step, budget: float, cut_timeout: float, min_volume_share: float = MIN_VOLUME_SHARE,
          diameters: dict | None = None):
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
        for unexplained, curved, region in regions(view, part, missing):
            candidates.append((unexplained, curved, view.name, region, depths))
    candidates.sort(key=lambda item: -item[0])  # most unexplained lines first; shading-only ones last
    step.log(f"{len(candidates)} region(s) to try, {sum(1 for c in candidates if c[1])} of them shaded like a cylinder")

    def evaluate(operation, argument, label: str):
        """Try one cut on the current solid: ((score, iso, solid, missing), None) if it passes the
        volume and silhouette checks and the iso views agree better, else (None, reason)."""
        try:
            carved = cutter.run(operation, solid, argument, cut_timeout)
        except Exception:
            carved = None
        if carved is None:
            return None, "failed"
        after = vh.volume(carved)
        if after >= vh.volume(solid) - 1e-6:
            return None, "nothing"
        if target and after < min_volume_share * target:
            return None, "too much"
        now = view_set.missing(carved)
        if any(now[v] - missing_now[v] > view_set.tolerance(v) for v in now):
            return None, "too much"  # opened background where the part is
        try:
            result = line_views.mismatch(carved)
        except Exception as error:  # the line drawing failed for this candidate: skip it
            step.log(f"could not draw the lines for {label} ({error!r})", level="warning")
            return None, "failed"
        score, iso = total(result), total(result, ISO_VIEWS)
        if iso >= current_iso:  # the isometric views must agree better too
            return None, "iso"
        return (score, iso, carved, now), None

    tried = kept = 0
    for unexplained, curved, view, region, depths in candidates:
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
        depths = region_depths(view, region, line_views, dims) or depths  # this region's own depths first
        options = [(f"flat pocket {depth:.2f} mm deep", cut_away,
                    replace(silhouette, depth=silhouette.depth + depth), True) for depth in depths[:MAX_DEPTHS]]
        circle = circle_of(region)
        if circle is not None:  # a circle in the view: the top of a cylinder standing on a face below
            options = [(label, operation, spec, False)
                       for label, spec, operation in boss_options(view, region, circle, dims, depths, views)] + options
        if curved:  # shaded like a cylinder: try cylinders first, flat pockets after (a light gradient
            # on a flat face looks the same, so the flat ones are not dropped)
            options = [(label, cut_curved, spec, False)
                       for label, spec in curved_options(view, region, silhouette, curved, dims, depths, diameters or {})
                       ] + options
        best = None
        flat_blocked = False
        for label, operation, argument, deeper_removes_more in options[:MAX_PER_REGION]:
            if time.monotonic() > deadline or tried >= MAX_TRIES:
                break
            if deeper_removes_more and flat_blocked:
                break
            tried += 1
            outcome, reason = evaluate(operation, argument, label)
            if outcome is None:
                flat_blocked = flat_blocked or (deeper_removes_more and reason == "too much")
                continue
            if best is None or outcome[0] < best[0]:
                best = (outcome[0], outcome[1], label, outcome[2], outcome[3])
        if best is not None and best[0] < current - max(MIN_GAIN_PX, MIN_GAIN * current):
            score, iso, label, carved, now = best
            rows, cols = np.where(region)
            h, w = region.shape
            span = (f"image x {cols.min() / (w - 1):.2f}-{cols.max() / (w - 1):.2f}, "
                    f"y {rows.min() / (h - 1):.2f}-{rows.max() / (h - 1):.2f} of the view")
            step.log(f"{view}: {label} at {span} ({unexplained} px of lines unexplained): "
                     f"mismatch {current} -> {score} px (iso {current_iso} -> {iso}), "
                     f"volume {vh.volume(solid):.0f} -> {vh.volume(carved):.0f} mm3")
            solid, current, current_iso, missing_now = carved, score, iso, now
            kept += 1
    # a part whose outline is round along an axis: try it as a turned part, after the pockets. Judged
    # by the isometric views: a revolved profile has a circle at every kink, which the straight views
    # count as extra lines although the part looks right from every side.
    for label, spec in revolve.whole_part_options(views, dims):
        outcome, reason = evaluate(revolve.cut_revolved, spec, label)
        if outcome is not None and outcome[1] < current_iso - max(MIN_GAIN_PX, MIN_GAIN * current_iso):
            step.log(f"{label}: iso mismatch {current_iso} -> {outcome[1]} px (all views {current} -> {outcome[0]}), "
                     f"volume {vh.volume(solid):.0f} -> {vh.volume(outcome[2]):.0f} mm3")
            current, current_iso, solid, missing_now = outcome
            kept += 1
        else:
            step.log(f"{label}: not kept ({reason or 'too little gain'})")
    step.metric(regions=len(candidates), tried=tried, kept=kept, line_mismatch=current)
    return solid, kept
