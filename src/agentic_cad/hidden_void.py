"""Step, deterministic: a part that looks right from every side and is far too heavy is hollow.

Once our solid fills every outline and has an edge under nearly every line of all eight views,
nothing a view shows is left to cut. If it then still has much more than the part's volume
(facts.json), the rest is material no view can see: a closed space inside. Its volume is known
exactly, its shape is not. The guess that keeps the same distance from every wall is taken: all
points of the solid deeper than one wall thickness under its surface, the thickness chosen so that
the space has the missing volume. (Practice 6: a pleated filter cone, right from outside, 2.77
times the true volume. A smaller copy of the solid does not stay inside it, and OpenCascade's
inward offset fails on it; so the space is found on a grid and built in layers.)

The space is built as a stack of prisms, one per layer of the grid, and taken out in one cut. A
closed space would make a second shell; a thin vent from its deepest point through the thinnest
wall along a box axis keeps the solid one shell, with the number of through-holes it had.

Only when every view is explained (at most LINES of its lines without an edge of ours), the excess
is large (EXCESS), and the result is one valid solid of the part's volume with the same number of
through-holes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad import hole_count
from agentic_cad import trace
from agentic_cad import verify
from agentic_cad import visual_hull as vh

ENABLED = os.environ.get("HULL_HIDDEN_VOID", "1") != "0"
EXCESS = float(os.environ.get("HULL_VOID_EXCESS", 1.5))    # our volume over the part's, at least
LINES = float(os.environ.get("HULL_VOID_LINES", 0.08))     # share of a view's lines without an edge of ours, at most
OUTLINE = 0.02      # share of a view's outline our solid may miss or overshoot
CELLS = 128         # grid cells along the longest side of the box
REACH = 24          # cells: walls are measured up to this thickness
MIN_WALL = 2.5      # cells: a thinner wall than this the grid cannot keep closed (2 cells broke through in 7 places)
GROUP = 3           # grid layers that make one prism: 74 prisms took over 5 minutes to cut, 19 take seconds
SMOOTH = 1.0        # cells a prism's outline may leave the grid's (it stays inside: the wall is thicker)
VOLUME = 0.05       # the result's volume may differ from the part's by this share
VENT = 0.004        # radius of the vent, as a share of the part's mean extent
TIMEOUT = float(os.environ.get("HULL_VOID_TIMEOUT", 120))


def unexplained(solid: TopoDS_Shape, observed: dict, dims: dict[str, float]) -> dict[str, tuple[float, float]]:
    """Per view: the share of its lines no edge of ours lies under, and of its outline we miss or overshoot."""
    mesh = verify.tessellate(solid, max(dims.values()) / 2000)
    result = {}
    for view, seen in observed.items():
        drawing = verify.draw(mesh, view, dims, seen.mm_per_px)
        near = cv2.dilate((drawing.sharp | drawing.edges).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        lines = float((seen.lines & ~near).sum() / max(seen.lines.sum(), 1))
        outline = float((drawing.mask ^ seen.mask).sum() / max(seen.mask.sum(), 1))
        result[view] = (lines, outline)
    return result


def occupancy(solid: TopoDS_Shape, dims: dict[str, float], cell: float) -> np.ndarray:
    """Which cells of a grid over the box lie in the solid: a line along z through every (x, y)
    cell, a little off the cell's middle so that it meets no edge, its crossings with the surface
    taken in pairs. A column with an odd number of crossings is left empty."""
    shape = tuple(int(np.ceil(dims[k] / cell)) for k in "xyz")
    triangles = verify.tessellate(solid, cell / 4).triangles
    xs = (np.arange(shape[0]) + 0.5137) * cell
    ys = (np.arange(shape[1]) + 0.4871) * cell
    crossings: dict[tuple[int, int], list[float]] = {}
    for a, b, c in triangles:
        low, high = np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)
        i0, i1 = np.searchsorted(xs, low[0]), np.searchsorted(xs, high[0])
        j0, j1 = np.searchsorted(ys, low[1]), np.searchsorted(ys, high[1])
        if i0 >= i1 or j0 >= j1:
            continue
        det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(det) < 1e-12:
            continue
        px, py = np.meshgrid(xs[i0:i1], ys[j0:j1], indexing="ij")
        u = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / det
        v = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / det
        w = 1 - u - v
        inside = (u >= 0) & (v >= 0) & (w >= 0)
        if not inside.any():
            continue
        z = u * a[2] + v * b[2] + w * c[2]
        for i, j in zip(*np.nonzero(inside)):
            crossings.setdefault((i0 + int(i), j0 + int(j)), []).append(float(z[i, j]))
    grid = np.zeros(shape, bool)
    zs = (np.arange(shape[2]) + 0.5) * cell
    for (i, j), values in crossings.items():
        values.sort()
        if len(values) % 2:
            continue
        for first, second in zip(values[::2], values[1::2]):
            grid[i, j, (zs >= first) & (zs <= second)] = True
    return grid


def depth_under_surface(grid: np.ndarray, reach: int) -> np.ndarray:
    """For every cell of the solid its distance to the nearest cell outside it, in cells, exact up to
    `reach`: the squared distance is carried along one axis after the other."""
    far = float((reach + 1) ** 2)
    squared = np.where(np.pad(grid, reach), far, 0.0)
    for axis in range(3):
        best = squared.copy()
        for shift in range(1, reach + 1):
            for sign in (1, -1):
                best = np.minimum(best, np.roll(squared, sign * shift, axis=axis) + shift * shift)
        squared = best
    inner = tuple(slice(reach, -reach) for _ in range(3))
    return np.sqrt(squared[inner])


def _simple(points: np.ndarray) -> bool:
    """Whether the closed polygon through `points` has no two sides that cross."""
    p = points.astype(float)
    q = np.roll(p, -1, axis=0)
    n = len(p)

    def side(a, b, c):
        return (b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0])

    for i in range(n):
        others = [j for j in range(i + 2, n) if not (i == 0 and j == n - 1)]
        if not others:
            continue
        a, b, c, d = p[i], q[i], p[others], q[others]
        if np.any((side(a, b, c) * side(a, b, d) < 0) & (side(c, d, a) * side(c, d, b) < 0)):
            return False
    return True


def _outlines(mask: np.ndarray) -> list[tuple[np.ndarray, list[np.ndarray]]]:
    """The areas of one layer as polygons in cell units: each an outer boundary with its holes."""
    # opened first: cells that hang together by a corner only would make an outline that touches itself
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    areas = []
    # with fewer corners where that leaves a simple polygon (a simplified outline can cross itself)
    simpler = [cv2.approxPolyDP(contour, SMOOTH, True) for contour in contours]
    contours = [new if len(new) >= 3 and _simple(new[:, 0, :]) else old for old, new in zip(contours, simpler)]
    for index, contour in enumerate(contours):
        if hierarchy[0][index][3] != -1 or len(contour) < 3 or cv2.contourArea(contour) < 4:
            continue
        holes, child = [], hierarchy[0][index][2]
        while child != -1:
            if len(contours[child]) >= 3 and cv2.contourArea(contours[child]) >= 4:
                holes.append(contours[child][:, 0, :].astype(float))
            child = hierarchy[0][child][0]
        areas.append((contour[:, 0, :].astype(float), holes))
    return areas


def layers(space: np.ndarray, cell: float) -> list[tuple[float, float, list]]:
    """The space as prisms along z: for each the height of its bottom, its own height and its areas
    in mm. A prism stands for GROUP layers of the grid and has what all of them share, so it stays
    inside the space. A mask is indexed [x, y]; OpenCV gives points as (column, row) = (y, x)."""
    result = []
    for number, k in enumerate(range(0, space.shape[2], GROUP)):
        block = space[:, :, k:k + GROUP]
        areas = _outlines(block.all(axis=2))
        if areas:
            # Every prism drawn in by a different hair towards the middle of the grid: outlines traced on
            # one grid share corners and sides from layer to layer, and two prisms that meet in a side
            # or a corner pinch the solid there.
            middle = np.array(space.shape[:2]) / 2
            shrink = 1 - 0.003 * (1 + number % 5)
            to_mm = lambda points: [(((p[1] - middle[0]) * shrink + middle[0] + 0.5) * cell,
                                     ((p[0] - middle[1]) * shrink + middle[1] + 0.5) * cell) for p in points]
            result.append((k * cell, block.shape[2] * cell,
                           [(to_mm(outer), [to_mm(hole) for hole in holes]) for outer, holes in areas]))
    return result


def layers_volume(stack: list) -> float:
    def area(points) -> float:
        p = np.array(points)
        return 0.5 * abs(float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1))))
    return sum(height * (area(outer) - sum(area(hole) for hole in holes))
               for _, height, areas in stack for outer, holes in areas)


def _wire(points, z: float):
    polygon = BRepBuilderAPI_MakePolygon()
    for x, y in points:
        polygon.Add(gp_Pnt(float(x), float(y), float(z)))
    polygon.Close()
    return polygon.Wire()


def remove_space(shape: TopoDS_Shape, argument) -> TopoDS_Shape | None:
    """`shape` without the layers of the space and without the vent, in one cut. For Cutter.run."""
    stack, vent = argument
    tools = List_TopoDS_Shape()
    for z, height, areas in stack:
        for outer, holes in areas:
            face = BRepBuilderAPI_MakeFace(_wire(outer, z))
            for hole in holes:   # OpenCV gives holes the other way round than outer boundaries, as a face needs them
                face.Add(_wire(hole, z))
            if not face.IsDone() or not BRepCheck_Analyzer(face.Face()).IsValid():
                return None
            # a hair taller than the layer, so that two layers share no face
            tools.Append(BRepPrimAPI_MakePrism(face.Face(), gp_Vec(0, 0, height * 1.002)).Shape())
    if vent is not None:
        start, direction, radius, length = vent
        frame = gp_Ax2(gp_Pnt(*map(float, start)), gp_Dir(*map(float, direction)))
        tools.Append(BRepPrimAPI_MakeCylinder(frame, float(radius), float(length)).Shape())
    arguments = List_TopoDS_Shape()
    arguments.Append(shape)
    operation = BRepAlgoAPI_Cut()
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.Build()
    if not operation.IsDone():
        return None
    solid = vh.largest_solid(operation.Shape())
    return solid if solid is not None and BRepCheck_Analyzer(solid).IsValid() else None


def plan(solid: TopoDS_Shape, dims: dict[str, float], missing: float):
    """The space of `missing` mm3: its layers, the cell size, the wall thickness in mm and the vent
    (start, direction, radius, length). None when no wall of MIN_WALL cells leaves that much."""
    cell = max(dims.values()) / CELLS
    grid = occupancy(solid, dims, cell)
    reach = min(REACH, int(min(grid.shape) // 2) + 1)
    depth = depth_under_surface(grid, reach)
    low, high = MIN_WALL, float(depth.max())
    if high <= low or layers_volume(layers(depth > low, cell)) < missing:
        return None
    for _ in range(18):   # the thickest wall whose space still has the missing volume
        middle = (low + high) / 2
        if layers_volume(layers(depth > middle, cell)) >= missing:
            low = middle
        else:
            high = middle
    space = depth > low
    stack = layers(space, cell)
    # the grid gives the volume in steps: the prisms are drawn in towards the box's axis until it is exact
    scale = float(np.sqrt(missing / layers_volume(stack)))
    centre = np.array([dims["x"], dims["y"]]) / 2
    pull = lambda points: [tuple((np.array(point) - centre) * scale + centre) for point in points]
    stack = [(z, height, [(pull(outer), [pull(hole) for hole in holes]) for outer, holes in areas]) for z, height, areas in stack]
    deepest = np.unravel_index(int(np.argmax(depth)), depth.shape)
    start = (np.array(deepest) + 0.5) * cell
    best = None
    for axis in range(3):
        for sign in (1, -1):
            index = list(deepest)
            steps = 0
            while 0 <= index[axis] < grid.shape[axis] and grid[tuple(index)]:
                index[axis] += sign
                steps += 1
            if best is None or steps < best[0]:
                direction = np.zeros(3)
                direction[axis] = sign
                best = (steps, direction)
    radius = VENT * float(np.mean(list(dims.values())))
    vent = (start, best[1], radius, (best[0] + 2) * cell)
    return stack, cell, low * cell, vent


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step."""
    with trace.step("hidden_void", kind="code", description=(
            "Hollows a solid that every view explains and that is still far heavier than the part")) as step:
        if not ENABLED or case_dir is None:
            return solid
        target = vh.read_volume(case_dir)
        if not target:
            return solid
        started = time.time()
        excess = vh.volume(solid) / target
        step.metric(vs_true_volume=round(excess, 3))
        if excess < EXCESS:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        observed = agent.evidence(case_dir, dims).observed
        fit = unexplained(solid, observed, dims)
        worst_lines = max(lines for lines, _ in fit.values())
        worst_outline = max(outline for _, outline in fit.values())
        step.metric(views=len(fit), worst_lines=round(worst_lines, 3), worst_outline=round(worst_outline, 3))
        if len(fit) < 8 or worst_lines > LINES or worst_outline > OUTLINE:
            step.log(f"the views are not all explained (lines {worst_lines:.2f}, outline {worst_outline:.3f}): "
                     "the excess may be something they show; nothing is hollowed")
            return solid
        planned = plan(solid, dims, vh.volume(solid) - target)
        if planned is None:
            step.log("no wall leaves a space of the missing volume; nothing is hollowed", level="warning")
            return solid
        stack, cell, wall, vent = planned
        holes_before = hole_count.genus(vh.tidy(solid))
        saved, agent.holes_stated = agent.holes_stated, None   # the closed space is judged here, on the result
        try:
            result = agent._guarded(step, solid, target, lambda: agent.cutter.run(
                remove_space, solid, (stack, vent), TIMEOUT), quiet=True, share=1 - VOLUME)
        finally:
            agent.holes_stated = saved
        if result is None:
            step.log("the hollowed solid is not a valid solid of the part's volume; kept as it was", level="warning")
            return solid
        volume, holes_after = vh.volume(result), hole_count.genus(vh.tidy(result))
        if abs(volume / target - 1) > VOLUME or holes_after != holes_before:
            step.log(f"the hollowed solid has {volume / target:.3f} times the part's volume and {holes_after} through-holes "
                     f"(before {holes_before}); kept as it was", level="warning")
            return solid
        step.metric(wall_mm=round(wall, 2), layers=len(stack), after_vs_true_volume=round(volume / target, 3),
                    seconds=round(time.time() - started, 1))
        step.log(f"every view is explained and the solid has {excess:.2f} times the part's volume: a space with walls "
                 f"of {wall:.1f} mm is taken out of it in {len(stack)} layers, with a vent of {2 * vent[2]:.2f} mm")
        agent.lines_found = None
        agent._record(step, result, "hidden_void")
        return result
