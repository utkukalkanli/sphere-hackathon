"""One list of coordinates per box axis, shared by the three silhouettes.

Each silhouette is fitted on its own pixel grid, so one plane of the part (say z = 14.0) comes out
as 14.00 in the front view and 14.08 in the right view. Cut one after the other, the two prisms then
leave a ledge a fraction of a pixel wide where the part has one sharp edge: a sliver face and its
side faces. Here the coordinates that the outlines of all three silhouettes hold fixed are collected
per box axis, those within 1.5 px of each other become one value, and the outlines are moved onto it
before anything is cut.

What gives a coordinate ("datum"):
- the box faces (exact, never moved);
- every line that is exactly horizontal or vertical in its outline;
- the leftmost, rightmost, lowest and highest point of every full circle and of every arc.
Each has a side, the side the material is on. Only datums with the same side are merged, so the two
faces of a thin wall or a thin slot are never moved onto each other. Merged datums take the value of
a box face if one is among them, else of the largest circle or arc (those are fitted over many pixels
and may sit on a radius of facts.json; they stay where they are), else the mean of the lines weighted
by their length.

A corner between two slanted lines is moved onto a datum that lies within the tolerance, but gives
none itself. Arcs stay circular: an arc that touches a moved line (a fillet) moves with it, and one
that crosses a moved line ends where the two now meet. No other arc and no full circle is moved: two
arcs of one circle must stay on one cylinder.
An outline that is no longer a valid face after this, or moved further than it should, is left as fitted.
"""

from __future__ import annotations

import math
import os
from dataclasses import replace

import numpy as np

from agentic_cad import outline_fit

# Off by default: few faces fewer, and a thin-walled part lost its walls (docs/memory/state.md)
ON = os.environ.get("HULL_DATUMS", "0") == "1"
TOL_PX = float(os.environ.get("HULL_DATUMS_PX", 1.5))   # datums this close together are one
MAX_MOVE = 2.5      # a vertex may end up this many tolerances from where it was fitted
ON_CIRCLE_PX = 0.02  # an arc's end counts as lying on its circle / touching a line within this
FIXED = float("inf")


class Table:
    """The merged datums of one case: per (axis, side) a sorted list of (low, high, target)."""

    def __init__(self, entries: list[tuple[str, int, float, float, bool]], tol: dict[str, float]):
        self.tol = tol
        self.clusters: dict[tuple[str, int], list[tuple[float, float, float]]] = {}
        self.merged = 0   # datums that were moved onto another one
        groups: dict[tuple[str, int], list[tuple[float, float, bool]]] = {}
        for axis, side, value, weight, rigid in entries:
            groups.setdefault((axis, side), []).append((value, weight, rigid))
        for key, members in groups.items():
            members.sort()
            parts = _split(members, tol[key[0]])
            self.clusters[key] = [self._merge(part) for part in parts]
            self.merged += sum(len(part) - 1 for part in parts)

    @staticmethod
    def _merge(members: list[tuple[float, float, bool]]) -> tuple[float, float, float]:
        values = np.array([m[0] for m in members])
        weights = np.array([m[1] for m in members])
        rigid = np.array([m[2] for m in members])
        if np.isinf(weights).any():
            target = float(values[np.isinf(weights)][0])
        elif rigid.any():
            target = float(values[rigid][np.argmax(weights[rigid])])
        else:
            target = float((values * weights).sum() / weights.sum())
        return float(values[0]), float(values[-1]), target

    def strong(self, axis: str, side: int, value: float) -> float:
        """Where a datum that was collected at `value` goes."""
        for low, high, target in self.clusters.get((axis, side), []):
            if low - 1e-9 <= value <= high + 1e-9:
                return target
        return value

    def near(self, axis: str, value: float) -> float:
        """The datum of either side within the tolerance of `value`, else `value`."""
        best = value
        reach = self.tol[axis]
        for side in (-1, 1):
            for _, _, target in self.clusters.get((axis, side), []):
                if abs(target - value) <= reach:
                    best, reach = target, abs(target - value)
        return best


def _split(members: list, tol: float) -> list[list]:
    """Sorted values in groups no wider than `tol`, split at the largest gaps."""
    if members[-1][0] - members[0][0] <= tol:
        return [members]
    gaps = [members[i + 1][0] - members[i][0] for i in range(len(members) - 1)]
    cut = int(np.argmax(gaps)) + 1
    return _split(members[:cut], tol) + _split(members[cut:], tol)


# -- reading a loop ------------------------------------------------------------------------------

def _circle_of(segment) -> tuple[np.ndarray, float] | None:
    start, middle, end = (np.array(p, dtype=float) for p in segment[1:])
    centre = outline_fit.circle_through(start, middle, end)
    if centre is None:
        return None
    return centre, float(np.hypot(*(start - centre)))


def _sweep(segment, centre: np.ndarray) -> tuple[float, float]:
    """Start angle and signed sweep of an arc."""
    a0, a1, a2 = (math.atan2(p[1] - centre[1], p[0] - centre[0]) for p in segment[1:])
    sweep = (a2 - a0) % (2 * math.pi)
    if (a1 - a0) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    return a0, sweep


def _constant(start: np.ndarray, end: np.ndarray) -> int | None:
    """The axis (0: a, 1: b) a line keeps exactly constant, if any."""
    delta = np.abs(end - start)
    for axis in (0, 1):
        if delta[axis] < 1e-9 and delta[1 - axis] > 1e-9:
            return axis
    return None


def _side(direction: np.ndarray, axis: int, material_left: bool) -> int:
    """On which side along `axis` the material lies, for a boundary walked in `direction`."""
    normal = np.array([-direction[1], direction[0]]) * (1 if material_left else -1)
    return 1 if normal[axis] > 0 else -1


def _extremes(segment, centre: np.ndarray, radius: float, material_left: bool) -> list[tuple[int, int, float]]:
    """(axis, side, value) for the axis-extreme points strictly inside an arc."""
    start, sweep = _sweep(segment, centre)
    found = []
    for quarter in range(4):
        angle = quarter * math.pi / 2
        turned = (angle - start) % (2 * math.pi) if sweep > 0 else (start - angle) % (2 * math.pi)
        if not math.radians(2) < turned < abs(sweep) - math.radians(2):
            continue
        axis = quarter % 2
        tangent = np.array([-math.sin(angle), math.cos(angle)]) * (1 if sweep > 0 else -1)
        value = centre[axis] + radius * (math.cos(angle) if axis == 0 else math.sin(angle))
        found.append((axis, _side(tangent, axis, material_left), float(value)))
    return found


def _material_left(loop: list[tuple], is_outer: bool) -> bool:
    return (outline_fit.signed_area(loop) > 0) == is_outer


def _collect(loop: list[tuple], is_outer: bool, axes: tuple[str, str], px: float) -> list[tuple[str, int, float, float, bool]]:
    """The datums one loop gives: (box axis, side, value, weight, whether it stays where it is)."""
    entries = []
    if loop[0][0] == "circle":
        (centre, radius) = loop[0][1], loop[0][2]
        for axis in (0, 1):
            for sign in (-1, 1):  # material is inside an outer circle, outside a hole
                side = -sign if is_outer else sign
                entries.append((axes[axis], side, centre[axis] + sign * radius, 2 * math.pi * radius / px, True))
        return entries
    left = _material_left(loop, is_outer)
    for segment in loop:
        if segment[0] == "line":
            start, end = np.array(segment[1]), np.array(segment[2])
            axis = _constant(start, end)
            if axis is not None:
                entries.append((axes[axis], _side(end - start, axis, left), float(start[axis]),
                                float(abs(end - start)[1 - axis]) / px, False))
        elif segment[0] == "arc":
            circle = _circle_of(segment)
            if circle is None:
                continue
            _, sweep = _sweep(segment, circle[0])
            for axis, side, value in _extremes(segment, *circle, left):
                entries.append((axes[axis], side, value, circle[1] * abs(sweep) / px, True))
    return entries


# -- moving a loop -------------------------------------------------------------------------------

class _Refused(Exception):
    pass


def _snap_loop(loop: list[tuple], is_outer: bool, axes: tuple[str, str], table: Table, px: float) -> list[tuple]:
    """The loop with its fixed coordinates moved onto the table's values."""
    if loop[0][0] == "circle":
        return loop
    count = len(loop)
    left = _material_left(loop, is_outer)
    old = np.array([segment[1] for segment in loop], dtype=float)
    new = old.copy()
    strong = np.zeros((count, 2), dtype=bool)
    placed = np.zeros(count, dtype=bool)   # vertices an arc has put on its circle
    constant: list[int | None] = [None] * count
    close = ON_CIRCLE_PX * px

    for i, segment in enumerate(loop):
        if segment[0] != "line":
            continue
        j = (i + 1) % count
        axis = _constant(old[i], old[j])
        constant[i] = axis
        if axis is None:
            continue
        target = table.strong(axes[axis], _side(old[j] - old[i], axis, left), float(old[i][axis]))
        for key in (i, j):
            new[key][axis] = target
            strong[key][axis] = True

    for i, segment in enumerate(loop):  # corners between two lines: onto a datum that is close by
        if segment[0] == "line" and loop[i - 1][0] == "line":
            for axis in (0, 1):
                if not strong[i][axis]:
                    new[i][axis] = table.near(axes[axis], float(old[i][axis]))

    middles: dict[int, np.ndarray] = {}
    for i, segment in enumerate(loop):
        if segment[0] != "arc":
            continue
        j = (i + 1) % count
        circle = _circle_of(segment)
        if circle is None:  # three points in a line: it is drawn as a line
            continue
        centre, radius = circle
        moved, moved_radius = centre.copy(), radius
        ends = ((i, (i - 1) % count), (j, j))   # (vertex, the segment on its other side)
        touching = {0: [], 1: []}                # axis -> [(vertex, side of the centre the line is on)]
        for key, other in ends:
            axis = constant[other] if loop[other][0] == "line" else None
            if axis is not None and abs(abs(old[key][axis] - centre[axis]) - radius) <= close \
                    and abs(old[key][1 - axis] - centre[1 - axis]) <= close:
                touching[axis].append((key, math.copysign(1.0, old[key][axis] - centre[axis])))
        for axis, found in touching.items():     # between two parallel lines: the radius follows them
            if len(found) == 2 and found[0][1] != found[1][1]:
                a, b = new[found[0][0]][axis], new[found[1][0]][axis]
                moved[axis], moved_radius = (a + b) / 2, abs(a - b) / 2
        for axis, found in touching.items():
            if len(found) == 1 or (len(found) == 2 and found[0][1] == found[1][1]):
                moved[axis] = new[found[0][0]][axis] - found[0][1] * moved_radius
        tangent = {key for found in touching.values() for key, _ in found}
        for key, other in ends:
            axis = constant[other] if loop[other][0] == "line" else None
            on_circle = abs(float(np.hypot(*(old[key] - centre))) - radius) <= close
            if key in tangent:
                new[key][1 - axis] = moved[1 - axis]
            elif axis is not None and on_circle:  # the arc crosses this line: where they meet now
                offset = new[key][axis] - moved[axis]
                if abs(offset) > moved_radius:
                    raise _Refused("an arc no longer reaches the line it ended on")
                reach = math.sqrt(moved_radius ** 2 - offset ** 2)
                new[key][1 - axis] = moved[1 - axis] + math.copysign(reach, old[key][1 - axis] - centre[1 - axis])
            elif axis is not None:
                new[key][1 - axis] = old[key][1 - axis] + (moved - centre)[1 - axis]
            elif not placed[key]:                 # next to a slanted line or another arc: carried along
                new[key] = moved + (old[key] - centre) * (moved_radius / radius)
            placed[key] = True
        direction = np.array(segment[2], dtype=float) - centre
        middles[i] = moved + direction / max(float(np.hypot(*direction)), 1e-12) * moved_radius

    if float(np.abs(new - old).max()) > MAX_MOVE * TOL_PX * px:
        raise _Refused("a vertex moved too far")
    result = []
    for i, segment in enumerate(loop):
        start, end = new[i], new[(i + 1) % count]
        if segment[0] == "arc" and i in middles:
            if float(np.hypot(*(end - start))) < 1e-6:
                raise _Refused("an arc closed up")
            result.append(("arc", tuple(map(float, start)), tuple(map(float, middles[i])), tuple(map(float, end))))
        elif float(np.hypot(*(end - start))) >= 1e-6:
            result.append(("line", tuple(map(float, start)), tuple(map(float, end))))
    if len(result) < 2:
        raise _Refused("the loop closed up")
    return result


def _valid(outline: dict) -> bool:
    """Whether an outline with its holes still makes one proper face."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepCheck import BRepCheck_Analyzer

    from agentic_cad import visual_hull as vh

    flat = vh.Silhouette(name="", views=[], mask=np.zeros((1, 1), np.uint8), outlines=[], origin=np.zeros(3),
                         right=vh.UNIT["x"], up=vh.UNIT["y"], direction=vh.UNIT["z"], depth=1.0)
    try:
        face = BRepBuilderAPI_MakeFace(vh._wire(outline["outer"], flat, True), True)
        for hole in outline["holes"]:
            face.Add(vh._wire(hole, flat, False))
        return face.IsDone() and BRepCheck_Analyzer(face.Face()).IsValid()
    except Exception:
        return False


def _same(a: dict, b: dict) -> bool:
    return a["outer"] == b["outer"] and a["holes"] == b["holes"]


# -- the step ------------------------------------------------------------------------------------

def align(silhouettes: dict, dims: dict[str, float]) -> tuple[dict, dict]:
    """`silhouettes`: name -> vh.Silhouette of the straight directions, as fitted. Returns the same
    with their outlines on shared datums (only the ones that changed), and a report."""
    from agentic_cad import visual_hull as vh

    pixel = {name: s.info["extent"][0] / max(s.mask.shape[1] - 1, 1) for name, s in silhouettes.items()}
    axes = {name: (vh.ORTHO[name]["u"], vh.ORTHO[name]["v"]) for name in silhouettes}
    tol = {axis: TOL_PX * max([pixel[n] for n in silhouettes if axis in axes[n]], default=0.0) for axis in "xyz"}

    entries = [(axis, side, value, FIXED, True) for axis in "xyz" for side, value in ((1, 0.0), (-1, dims[axis]))]
    for name, silhouette in silhouettes.items():
        for outline in silhouette.outlines:
            for loop, is_outer in [(outline["outer"], True)] + [(hole, False) for hole in outline["holes"]]:
                entries += _collect(loop, is_outer, axes[name], pixel[name])
    table = Table(entries, tol)

    aligned, report = {}, {"datums": len(entries), "merged": table.merged, "refused": 0}
    for name, silhouette in silhouettes.items():
        outlines, changed = [], False
        for outline in silhouette.outlines:
            try:
                moved = {"outer": _snap_loop(outline["outer"], True, axes[name], table, pixel[name]),
                         "holes": [_snap_loop(hole, False, axes[name], table, pixel[name]) for hole in outline["holes"]]}
            except Exception:
                moved = None
            if moved is None or _same(moved, outline):
                outlines.append(outline)
            elif _valid(moved):
                outlines.append(moved)
                changed = True
            else:
                outlines.append(outline)
                moved = None
            report["refused"] += moved is None
        if changed:
            aligned[name] = replace(silhouette, outlines=outlines, info={**silhouette.info, "datums": True})
    return aligned, report


def aligned_silhouettes(views: list, dims: dict[str, float], names: list[str], hints) -> tuple[dict, dict]:
    """Fit the straight silhouettes among `names` and put them on shared datums. `hints(name)` gives
    the known circles of a direction (as for vh.ortho_silhouette)."""
    from agentic_cad import visual_hull as vh

    fitted = {}
    for name in names:
        if name in vh.ORTHO:
            silhouette = vh.ortho_silhouette(name, views, dims, hints(name))
            if silhouette is not None:
                fitted[name] = silhouette
    if len(fitted) < 2:
        return {}, {}
    return align(fitted, dims)
