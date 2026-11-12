"""Turned shapes: cylinders, cones, domes and flanges read as a profile revolved about an axis.

A circle in a straight view is the end of something round along that view's axis: the whole part
(a turned part whose outline in that view is a circle) or a boss standing on a face. The silhouettes
leave such a shape square in section. Its true section is round, and its radius at each height can
be read from the two side views: half the width of the silhouette there, measured from the axis. A
cylinder gives a constant radius, a cone a straight slope, a dome a curve; revolving that profile
about the axis gives the shape, and whatever the square block has outside it is cut.

The radius is taken as the largest either side view shows at that height, so a part that is not
quite round (fins, flats) keeps its material; the checks in `line_cuts` (true volume, every
silhouette including the isometric ones, the line drawings) decide whether the cut stays.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeRevol
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import visual_hull as vh
from agentic_cad.views import ORTHO_VIEWS

PROFILE_TOLERANCE_PX = 6.0  # a profile point more than this off the line through its neighbours is a kink
ROUND_OUTLINE = 0.95       # area / enclosing circle's area of a view's outline that counts as round
# the views that look along each axis, and the two side views that show it lengthwise
ALONG = {0: ("left", "right"), 1: ("front", "back"), 2: ("top", "bottom")}
SIDES = {0: ("front", "top"), 1: ("right", "top"), 2: ("front", "right")}


def to_true(view: str, shape: tuple[int, int], dims: np.ndarray):
    """Pixel (col, row) of a cropped straight view -> true coordinates along its (u, v) axes."""
    (u, flip_u), (v, flip_v) = ORTHO_VIEWS[view]
    height, width = shape

    def convert(cols, rows):
        a = np.asarray(cols, float) / (width - 1) * dims[u]
        b = (height - 1 - np.asarray(rows, float)) / (height - 1) * dims[v]
        return (dims[u] - a if flip_u else a), (dims[v] - b if flip_v else b)
    return u, v, convert


def round_outline(views: list[Path], axis: int, dims: np.ndarray) -> tuple[float, float, float] | None:
    """If the view along `axis` has a round outline: its centre (in the two other true coordinates,
    in index order) and radius, in mm."""
    for name in ALONG[axis]:
        path = vh.find_view(views, name)
        mask = vh.part_mask(path) if path else None
        if mask is None:
            continue
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        contour = max(contours, key=cv2.contourArea)
        (col, row), radius_px = cv2.minEnclosingCircle(contour)
        # a pixel outline's perimeter is too long (it is a staircase): compare the area with the
        # enclosing circle's instead (1 for a circle, 0.64 for a square)
        if radius_px <= 0 or cv2.contourArea(contour) / (np.pi * radius_px ** 2) < ROUND_OUTLINE:
            return None
        u, v, convert = to_true(name, mask.shape, dims)
        cu, cv = convert([col], [row])
        centre = {u: float(cu[0]), v: float(cv[0])}
        px = (dims[u] / (mask.shape[1] - 1) + dims[v] / (mask.shape[0] - 1)) / 2
        others = sorted(centre)
        return centre[others[0]], centre[others[1]], radius_px * px
    return None


def profile(views: list[Path], axis: int, centre: dict[int, float], dims: np.ndarray,
            window: float, start: float, end: float) -> np.ndarray | None:
    """(height along the axis, radius) from `start` to `end`: per height the largest distance from
    the axis at which a side view shows the part, within `window` of the axis. None if unreadable."""
    heights = np.linspace(start, end, max(8, int(abs(end - start) / dims[axis] * 400)))
    radius = np.zeros(len(heights))
    seen = False
    for name in SIDES[axis]:
        path = vh.find_view(views, name)
        mask = vh.part_mask(path) if path else None
        if mask is None:
            continue
        u, v, convert = to_true(name, mask.shape, dims)
        other = v if u == axis else u
        rows, cols = np.where(mask)
        a, b = convert(cols, rows)
        along, across = (a, b) if u == axis else (b, a)
        offset = np.abs(across - centre[other])
        keep = offset <= window
        if not keep.any():
            continue
        seen = True
        step = dims[axis] / (max(mask.shape) - 1)
        index = np.clip(np.round((along[keep] - start) / (heights[1] - heights[0])).astype(int), 0, len(heights) - 1)
        inside = (along[keep] >= min(start, end) - step) & (along[keep] <= max(start, end) + step)
        np.maximum.at(radius, index[inside], offset[keep][inside])
    if not seen or radius.max() <= 0:
        return None
    pixel = dims.max() / 1000
    padded = np.pad(radius, 1, mode="edge")  # not np.roll: it would carry one end's radius to the other
    radius = np.maximum(np.maximum(padded[:-2], padded[1:-1]), padded[2:]) + pixel  # never thinner
    radius = np.maximum(radius, pixel)
    # fewer points: drop those on a straight line between their neighbours (within half a pixel)
    points = np.column_stack([heights, radius])
    # every kink becomes a circular edge: keep only kinks more than PROFILE_TOLERANCE_PX off a straight line
    simplified = cv2.approxPolyDP((points / pixel).astype(np.float32).reshape(-1, 1, 2), PROFILE_TOLERANCE_PX, False)
    return simplified.reshape(-1, 2) * pixel


def revolved(axis: int, centre: dict[int, float], points: np.ndarray) -> TopoDS_Shape:
    """The profile (height, radius) revolved a full turn about the axis through `centre`."""
    others = [i for i in range(3) if i != axis]
    radial = np.zeros(3)
    radial[others[0]] = 1.0
    base = np.zeros(3)
    for i in others:
        base[i] = centre[i]
    unit = np.zeros(3)
    unit[axis] = 1.0

    def point(h, r):
        p = base + unit * h + radial * r
        return gp_Pnt(*map(float, p))

    polygon = BRepBuilderAPI_MakePolygon()
    polygon.Add(point(points[0][0], 0.0))
    for h, r in points:
        polygon.Add(point(h, r))
    polygon.Add(point(points[-1][0], 0.0))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face()
    return BRepPrimAPI_MakeRevol(face, gp_Ax1(point(0, 0), gp_Dir(*map(float, unit)))).Shape()


def cut_revolved(shape: TopoDS_Shape, spec: dict) -> TopoDS_Shape | None:
    """Keep only what lies inside the revolved profile, within the block `low`..`high` (everything
    of `shape` outside the block is kept). For `Cutter.run`."""
    from agentic_cad.line_cuts import _valid

    turned = revolved(spec["axis"], spec["centre"], np.asarray(spec["profile"], float))
    if spec.get("whole"):
        operation = BRepAlgoAPI_Common(shape, turned)
    else:
        low, high = np.asarray(spec["low"], float), np.asarray(spec["high"], float)
        block = BRepPrimAPI_MakeBox(gp_Pnt(*map(float, low)), gp_Pnt(*map(float, high))).Shape()
        operation = BRepAlgoAPI_Cut(shape, BRepAlgoAPI_Cut(block, turned).Shape())
    return _valid(operation.Shape()) if operation.IsDone() else None


def whole_part_options(views: list[Path], dims: np.ndarray) -> list[tuple[str, dict]]:
    """For every axis along which the part's outline is round: the part as a turned part."""
    options = []
    for axis in range(3):
        found = round_outline(views, axis, dims)
        if found is None:
            continue
        others = [i for i in range(3) if i != axis]
        centre = {others[0]: found[0], others[1]: found[1]}
        points = profile(views, axis, centre, dims, float(dims.max()), 0.0, float(dims[axis]))
        if points is None:
            continue
        options.append((f"turned about {'xyz'[axis]} (outline d {2 * found[2]:.1f}, {len(points)} profile points)",
                        {"axis": axis, "centre": centre, "profile": points, "whole": True}))
    return options


def boss_option(views: list[Path], dims: np.ndarray, axis: int, centre: dict[int, float], radius: float,
                face: float, base: float) -> tuple[str, dict] | None:
    """A boss of the given outline radius standing between `face` (the viewer's side, a true
    coordinate along `axis`) and `base`, shaped by the side views' profile: a cylinder, a cone, a dome."""
    points = profile(views, axis, centre, dims, radius * 1.05, base, face)
    if points is None:
        return None
    low, high = np.zeros(3), np.zeros(3)
    for i, c in centre.items():
        low[i], high[i] = c - radius * 1.05, c + radius * 1.05
    low[axis], high[axis] = min(face, base), max(face, base)
    shape = "cone" if abs(points[0][1] - points[-1][1]) > 0.1 * radius else "cylinder"
    return (f"{shape}-like boss d {2 * radius:.1f} from {base:.1f} to {face:.1f} along {'xyz'[axis]}",
            {"axis": axis, "centre": centre, "profile": points, "low": low, "high": high})
