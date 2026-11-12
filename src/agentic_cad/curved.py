"""Step after the rounds, deterministic: curved faces around an axis that no silhouette shows.

Seen along a boss or bore axis of facts.json, a face of revolution is a ring around the axis. Where
the part has a recess behind a rim there (the dish of a valve head, a groove in an end face), the
side views only show the rim, and the solid has a flat ring at the rim's level instead.

The view along the axis tells: the ring's tone changes across it, so the face is curved
(regions.py), while the solid's face there is flat. Next to the flat ring the solid usually has the
start of the real face, from the side silhouette: an arc or a slope that ends where the rim hides
it. That piece is fitted (a straight line or a circle; a circle's radius is snapped to a torus radius
of facts.json when one is close) and carried on under the flat ring. The cut is proposed only if the
continuation stays below the flat level and either comes back up to it at the other end of the ring
or ends at a wall.

Where the solid shows no start of the face at all (a conical recess that begins at the rim), the
candidates come from facts.json: a cone of each listed half angle falling from one end of the ring,
and a torus of each listed radius spanning it. They are alternatives for the same ring: the one the
verifier rates highest is kept, and its score includes how close the volume comes to the true one.

The tone table of the renderer (tone.py) is used as a check only: a candidate is dropped if the tones
it would have around the ring are more than 10 grey levels off (median), or fit the view worse than
those of the flat face. Without it the verifier accepts wrong cuts that bring the volume closer
(practice part 5: a 5 degree cone through the whole part). The table is not good enough to choose
between two cone angles ten degrees apart (eval part 249).

What is cut is the body of revolution between the flat level and the profile. Nothing is kept that
the verifier rates lower or that leaves less than the part's volume.
"""

from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad import export
from agentic_cad import holes as hl
from agentic_cad import regions, tone, trace, verify
from agentic_cad import visual_hull as vh

ANGLES = 180            # directions around the axis in which the view is read
ANGULAR = 0.05          # radians between neighbouring triangles when the solid is drawn: its curves must be smooth
SHARE = 0.9             # of the directions around the axis that must agree for a radius to count
MIN_RING_PX = 6         # narrower flat rings are left alone
GAP_PX = 12             # rings this close, the solid flat in between, have only a line between them
MIN_PIECE_PX = 6        # the visible start of the face must be at least this wide to be continued
MIN_DROP_PX = 2.0       # a recess shallower than this is not cut
LEVEL_PX = 1.0          # the flat ring is flat to within this
JOIN_PX = 2.0           # the continued profile meets the flat level this closely where they join
FIT_PX = 0.7            # the visible piece lies this close to the fitted line or circle
MAX_MISFIT = 10.0       # grey levels: the tones of a proposed face are this close to the view's (median)
SNAP = 0.05             # a fitted radius this close (relative) to one of facts.json is replaced by it
MAX_AXES = 12
MAX_PASSES = 3
MAX_RINGS = 6           # flat rings cut per case, the largest first
MIN_SHARE = 0.97        # as in hull_agent: no cut may leave less than this share of the true volume
MIN_EXCESS = 1.02       # the step is skipped when the solid has less than this times the true volume
TIMEOUT = float(os.environ.get("HULL_CURVED_TIMEOUT", 30))
SCORE_SLACK = 0.003


@dataclass
class Dish:
    view: str
    origin: tuple[float, float, float]    # the point of the axis at height 0
    towards: tuple[float, float, float]   # unit vector along the axis, towards the camera
    radial: tuple[float, float, float]    # unit vector across it
    level: float                          # height of the flat ring
    inner: float                          # radii between which the profile is below the level
    outer: float
    profile: list                         # ("arc", start, middle, end) or ("line", start, end), points (radius, height), inner to outer
    how: str                              # what was fitted
    drop: float                           # deepest point below the level, mm
    ring: tuple = ()                      # which flat ring it replaces: dishes with the same one are alternatives
    removes: float = 0.0                  # mm3 it takes out of the solid
    misfit: float | None = None           # median difference between the ring's tones and those such a face would have
    flat_misfit: float | None = None      # ... and those the flat face of the solid would have

    def summary(self) -> dict:
        return {"view": self.view, "inner_radius_mm": round(self.inner, 2), "outer_radius_mm": round(self.outer, 2),
                "drop_mm": round(self.drop, 2), "removes_mm3": round(self.removes, 1), "fitted": self.how,
                "tone_misfit": None if self.misfit is None else round(self.misfit, 1),
                "tone_misfit_flat": None if self.flat_misfit is None else round(self.flat_misfit, 1)}


def _numbers(table) -> list[float]:
    result = []
    for value in (table or {}):
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            pass
    return result


def _axis_lines(case_dir: Path) -> tuple[list[tuple[str, dict[str, float]]], list[float], list[float]]:
    """The boss and bore axes of facts.json that run along x, y or z (largest first), the radii it
    lists for tori and the half angles it lists for cones."""
    try:
        facts = json.loads((case_dir / "facts.json").read_text())
    except Exception:
        return [], [], []
    found: dict[tuple, float] = {}
    exact: dict[tuple, dict[str, float]] = {}
    for cylinder in hl._cylinders(facts.get("cylindrical_shafts_and_bosses")) + hl._cylinders(facts.get("cylindrical_holes_and_bores")):
        letters = [letter for index, letter in enumerate("xyz") if abs(cylinder.axis[index]) > 0.9999]
        if letters:
            key = (letters[0], *(round(cylinder.centre[i], 1) for i, l in enumerate("xyz") if l != letters[0]))
            found[key] = max(found.get(key, 0.0), cylinder.radius)
            exact.setdefault(key, {l: float(cylinder.centre[i]) for i, l in enumerate("xyz") if l != letters[0]})
    ordered = sorted(found, key=lambda key: -found[key])[:MAX_AXES]
    lines = [(key[0], exact[key]) for key in ordered]
    angles = [a for a in _numbers(facts.get("cones_half_angle_deg")) if 1.0 <= a <= 89.0]
    return lines, _numbers(facts.get("fillets_from_tori_minor_radii")), angles


def _points(profile: list, per_arc: int = 24) -> list[tuple[float, float]]:
    """The profile as points (radius, height), arcs sampled."""
    points = [profile[0][1]]
    for segment in profile:
        if segment[0] == "arc":
            (r0, h0), (rm, hm), (r1, h1) = segment[1:]
            # the circle through the three points; start and end are at the same height
            half = (r1 - r0) / 2
            sag = h0 - hm
            size = (half ** 2 + sag ** 2) / (2 * sag)
            for r in np.linspace(r0, r1, per_arc + 1)[1:]:
                points.append((float(r), float(hm + size - np.sqrt(max(size ** 2 - (r - rm) ** 2, 0.0)))))
        else:
            points.append(segment[2])
    return points


def _stretches(flags: np.ndarray) -> list[tuple[int, int]]:
    padded = np.concatenate([[False], flags, [False]])
    change = np.nonzero(padded[1:] != padded[:-1])[0]
    return [(int(a), int(b) - 1) for a, b in zip(change[::2], change[1::2])]


def _fit(radius: np.ndarray, height: np.ndarray, pixel: float, known_radii: list[float]):
    """A function height(radius) through the points, and its name: a straight line if that fits,
    else a circle. None if neither does."""
    line = np.polyfit(radius, height, 1)
    if np.abs(np.polyval(line, radius) - height).max() <= FIT_PX * pixel:
        return (lambda r: np.polyval(line, r)), "line", None
    # circle through the points, by least squares on r^2 + h^2 + a r + b h + c = 0
    design = np.c_[radius, height, np.ones(len(radius))]
    a, b, c = np.linalg.lstsq(design, -(radius ** 2 + height ** 2), rcond=None)[0]
    centre = np.array([-a / 2, -b / 2])
    size = float(np.sqrt(max(centre @ centre - c, 0.0)))
    name = f"circle R{size:.2f}"
    for known in known_radii:
        if abs(known - size) <= SNAP * known:
            for _ in range(20):  # the same circle with the listed radius: move its centre onto the points
                offset = np.c_[radius, height] - centre
                distance = np.maximum(np.linalg.norm(offset, axis=1), 1e-9)
                centre = centre + (offset * ((distance - known) / distance)[:, None]).mean(axis=0)
            size, name = known, f"circle R{known:g} of facts.json"
            break
    side = 1.0 if np.mean(height) > centre[1] else -1.0   # which half of the circle the points are on

    def circle(r):
        inside = size ** 2 - (np.asarray(r, dtype=float) - centre[0]) ** 2
        return np.where(inside >= 0, centre[1] + side * np.sqrt(np.maximum(inside, 0.0)), np.nan)

    # judged by the distance to the circle, not by the height: where the profile is steep, a pixel's
    # width in radius is many pixels in height
    distance = np.abs(np.hypot(radius - centre[0], height - centre[1]) - size)
    if distance.max() > FIT_PX * pixel or np.isnan(circle(radius)).any():
        return None
    return circle, name, (float(centre[0]), float(centre[1]), size, side)


def find_in_view(view: str, position: dict[str, float], observed: verify.Observed, labels: np.ndarray,
                 items: list, draw, dims: dict[str, float], known_radii: list[float],
                 known_angles: list[float] = (), choose_table=None) -> list[Dish]:
    """The flat rings of the solid around this axis that the view shows as curved, each with the
    profile that replaces it. `draw()` gives the solid drawn from this view and `choose_table()` the
    tone table; both are only asked for when the view itself shows a curved ring around the axis,
    because drawing the solid is most of what this step costs."""
    right, up, towards = verify.camera(view)
    pixel = observed.mm_per_px
    size = observed.mask.shape[0]
    box_centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    on_axis = np.array([position.get(letter, box_centre[index]) for index, letter in enumerate("xyz")]) - box_centre
    column, row = on_axis @ right / pixel + size / 2 - 0.5, size / 2 - 0.5 - on_axis @ up / pixel

    count = int(min(size, np.hypot(size, size) / 2))
    radius_px = np.arange(count)[:, None]
    angle = np.linspace(0, 2 * np.pi, ANGLES, endpoint=False)[None, :]
    cols = np.round(column + radius_px * np.cos(angle)).astype(int)
    rows = np.round(row - radius_px * np.sin(angle)).astype(int)
    inside = (cols >= 0) & (cols < size) & (rows >= 0) & (rows < size)
    cols, rows = np.clip(cols, 0, size - 1), np.clip(rows, 0, size - 1)

    curved_labels = np.zeros(int(labels.max()) + 1, bool)
    curved_labels[[item.label for item in items if item.kind == "curved"]] = True
    in_view = inside & observed.mask[rows, cols] & curved_labels[labels[rows, cols]]
    if not any(b - a + 1 >= MIN_RING_PX for a, b in _stretches(in_view.mean(axis=1) >= SHARE)):
        return []   # the view shows no curved ring around this axis: nothing to compare the solid with
    drawing = draw()
    table = None
    part = inside & drawing.mask[rows, cols] & observed.mask[rows, cols]
    seen_curved = part & curved_labels[labels[rows, cols]]
    facing = part & (drawing.normal[rows, cols] @ towards > 0.9995)
    depth = np.where(part, drawing.depth[rows, cols], np.nan)

    tones = regions.tone_of(observed.image).astype(float)
    readable = regions._readable(observed)
    whole = part.mean(axis=1) >= SHARE                      # the part goes all the way round at this radius
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # radii at which the solid is not there at all
        height = np.where(whole, np.nanmedian(depth, axis=1), np.nan)
        spread = np.nanpercentile(depth, 90, axis=1) - np.nanpercentile(depth, 10, axis=1)
    flat = whole & (facing.mean(axis=1) >= SHARE) & (spread <= LEVEL_PX * pixel)
    curved = whole & (seen_curved.mean(axis=1) >= SHARE)
    radius = np.arange(count) * pixel

    found = []
    # Rings that follow each other with only a line between them, the solid flat across it, are one
    # recess: its face goes on through the line (or has several faces; one profile is tried for all).
    rings: list[tuple[int, int]] = []
    for first, last in _stretches(flat & curved):
        if rings and first - rings[-1][1] - 1 <= GAP_PX and flat[rings[-1][1] + 1:first].all():
            rings[-1] = (rings[-1][0], last)
        else:
            rings.append((first, last))
    for first, last in rings:
        if last - first + 1 < MIN_RING_PX:
            continue
        level = float(np.median(height[first:last + 1]))
        if np.abs(height[first:last + 1] - level).max() > LEVEL_PX * pixel:
            continue
        # the visible start of the real face on either side: curved in the view, not flat in the solid
        pieces = []
        for step, start in ((-1, first - 2), (1, last + 2)):
            index, chosen = start, []
            while 0 < index < count and curved[index] and not flat[index]:
                chosen.append(index)
                index += step
            if len(chosen) >= MIN_PIECE_PX:
                fitted = _fit(radius[chosen], height[chosen], pixel, known_radii)
                if fitted is not None:
                    pieces.append((step, *fitted))
        origin = box_centre + on_axis - (on_axis @ towards) * towards
        low, high = float(radius[first]), float(radius[last])

        def dish(profile, how, drop):
            # volume between the level and the profile, exact for each straight piece
            points = [(r, level - h) for r, h in _points(profile)]
            removes = float(sum(2 * np.pi * (r1 - r0) * (r0 * d0 / 3 + r1 * d1 / 3 + r0 * d1 / 6 + r1 * d0 / 6)
                                for (r0, d0), (r1, d1) in zip(points[:-1], points[1:])))
            result = Dish(view, tuple(map(float, origin)), tuple(map(float, towards)), tuple(map(float, right)), level,
                          profile[0][1][0], profile[-1][-1][0], profile, how, float(drop), (view, first, last), removes)
            nonlocal table
            if table is None and choose_table is not None:
                table = choose_table()
            if table is not None:
                # the tones such a face would have around the ring, against the ones the view shows
                across, down = np.array(_points(profile)).T
                inside = np.nonzero((radius >= across[0] + 2 * pixel) & (radius <= across[-1] - 2 * pixel))[0]
                if len(inside):
                    slope = np.interp(radius[inside], (across[:-1] + across[1:]) / 2, np.diff(down) / np.maximum(np.diff(across), 1e-9))
                    slope = np.broadcast_to(slope[:, None], (len(inside), ANGLES))
                    normal = np.stack([-slope * np.cos(angle), -slope * np.sin(angle), np.ones_like(slope)], axis=2)
                    normal /= np.linalg.norm(normal, axis=2, keepdims=True)
                    usable = readable[rows[inside], cols[inside]] & part[inside]
                    if usable.sum() >= 50:
                        seen = tones[rows[inside], cols[inside]][usable]
                        result.misfit = float(np.median(np.abs(seen - table.tone(normal)[usable])))
                        result.flat_misfit = float(np.median(np.abs(seen - table.tone(np.array([0.0, 0.0, 1.0])))))
            return result

        if not pieces:
            # nothing of the real face shows in the solid: the cones and tori facts.json lists
            def at(index):
                return height[index] if 0 <= index < count and whole[index] else np.nan
            inside, outside = at(first - 3), at(last + 3)
            width = high - low
            for half_angle in known_angles:
                fall = width / np.tan(np.radians(half_angle))
                if fall < MIN_DROP_PX * pixel:
                    continue
                # falling towards the axis: not if the solid goes on at the same level inside the ring
                if first <= 2 or not abs(inside - level) <= JOIN_PX * pixel:
                    found.append(dish([("line", (low, level - fall), (high, level))],
                                      f"cone {half_angle:g} of facts.json, towards the axis", fall))
                # falling away from the axis: only against a wall
                if outside > level + JOIN_PX * pixel:
                    found.append(dish([("line", (low, level), (high, level - fall))],
                                      f"cone {half_angle:g} of facts.json, away from the axis", fall))
            for known in known_radii:
                if known > width / 2 and low > 0:
                    sag = known - float(np.sqrt(known ** 2 - (width / 2) ** 2))
                    if sag >= MIN_DROP_PX * pixel:
                        found.append(dish([("arc", (low, level), ((low + high) / 2, level - sag), (high, level))],
                                          f"circle R{known:g} of facts.json across the ring", sag))
            continue
        grid = np.linspace(radius[first], radius[last], 4 * (last - first) + 1)
        candidates = np.array([function(grid) for _, function, _, _ in pieces])
        if np.isnan(candidates).any():
            continue
        joined = all(abs(float(function(radius[first if step < 0 else last])) - level) <= JOIN_PX * pixel
                     for step, function, _, _ in pieces)
        surface = candidates.max(axis=0)
        if not joined or (surface > level + LEVEL_PX * pixel).any():
            continue
        drop = level - float(surface.min())
        if drop < MIN_DROP_PX * pixel:
            continue
        if len(pieces) == 1:  # the far end: back up at the level, or at a wall of the solid
            step = pieces[0][0]
            far = last if step < 0 else first
            returns = level - float(surface[-1 if step < 0 else 0]) <= JOIN_PX * pixel
            beyond = far - 3 * step
            wall = 0 <= beyond < count and bool(whole[beyond]) and height[beyond] > level + JOIN_PX * pixel
            if not (returns or wall):
                continue

        profile, how = None, " and ".join(name for _, _, name, _ in pieces)
        circle = pieces[0][3]
        if len(pieces) == 1 and circle is not None and circle[3] < 0 and abs(level - circle[1]) < circle[2]:
            # one circle that dips below the level and comes back: cut exactly between its two crossings
            half = float(np.sqrt(circle[2] ** 2 - (level - circle[1]) ** 2))
            start, end = circle[0] - half, circle[0] + half
            if abs(start - low) <= 2 * JOIN_PX * pixel and abs(end - high) <= 2 * JOIN_PX * pixel and start > 0:
                low, high = start, end
                profile = [("arc", (low, level), (circle[0], circle[1] - circle[2]), (high, level))]
        if profile is None:
            keep = np.linspace(0, len(grid) - 1, min(len(grid), 49)).astype(int)
            points = [(float(grid[k]), float(min(surface[k], level))) for k in keep]
            profile = [("line", a, b) for a, b in zip(points[:-1], points[1:])]
        found.append(dish(profile, how, drop))
    # Not proposed: a face that would reach through the far side of the box, and a face whose tones
    # do not fit the ring: worse than the flat face of the solid does, or just badly
    floor = -float(abs(towards) @ np.array([dims["x"], dims["y"], dims["z"]])) / 2
    return [d for d in found if d.level - d.drop > floor + JOIN_PX * pixel
            and (d.misfit is None or d.flat_misfit is None or (d.misfit < d.flat_misfit and d.misfit <= MAX_MISFIT))]


def find(solid: TopoDS_Shape, case_dir: Path, observed: dict, dims: dict[str, float]) -> list[Dish]:
    lines, known_radii, known_angles = _axis_lines(case_dir)
    if not lines or not observed:
        return []
    areas = regions.find(observed)
    drawings, chosen = {}, []

    def drawn(name: str) -> verify.Drawing:
        if name not in drawings:
            if "mesh" not in drawings:
                # on a copy: a triangulation stays on the shape it was made for, and the steps after
                # this one would draw the solid from this finer one and decide differently
                copy = BRepBuilderAPI_Copy(solid, True, False).Shape()
                drawings["mesh"] = verify.tessellate(copy, 0.3 * min(o.mm_per_px for o in observed.values()), ANGULAR)
            seen = observed[name]
            drawings[name] = verify.draw(drawings["mesh"], name, dims, seen.mm_per_px, seen.mask.shape[0])
        return drawings[name]

    def choose_table() -> tone.Table | None:
        if not chosen:   # needs the solid drawn from every camera: only once a face is proposed
            chosen.append(tone.choose(observed, {name: drawn(name) for name in observed}))
        return chosen[0]

    found = []
    for axis, position in lines:
        for view in verify.ORTHO_VIEWS:
            if view in areas and abs(verify.camera(view)[2]["xyz".index(axis)]) > 0.999:
                labels, items = areas[view]
                found += find_in_view(view, position, observed[view], labels, items, lambda view=view: drawn(view),
                                      dims, known_radii, known_angles, choose_table)
    return found


def _tool(dish: Dish) -> TopoDS_Shape:
    """The body of revolution between the flat level (and a little above) and the profile."""
    origin, towards, radial = (np.array(v) for v in (dish.origin, dish.towards, dish.radial))

    def point(radius: float, height: float) -> gp_Pnt:
        return gp_Pnt(*map(float, origin + radius * radial + height * towards))

    above = dish.level + 0.02 * (dish.outer - dish.inner)
    wire = BRepBuilderAPI_MakeWire()
    start, end = dish.profile[0][1], dish.profile[-1][-1]
    for a, b in (((dish.inner, above), (dish.outer, above)), ((dish.outer, above), end)):
        wire.Add(BRepBuilderAPI_MakeEdge(point(*a), point(*b)).Edge())
    for segment in reversed(dish.profile):   # back from the outer end to the inner one
        if segment[0] == "arc":
            arc = GC_MakeArcOfCircle(point(*segment[3]), point(*segment[2]), point(*segment[1]))
            wire.Add(BRepBuilderAPI_MakeEdge(arc.Value()).Edge())
        else:
            wire.Add(BRepBuilderAPI_MakeEdge(point(*segment[2]), point(*segment[1])).Edge())
    wire.Add(BRepBuilderAPI_MakeEdge(point(*start), point(dish.inner, above)).Edge())
    face = BRepBuilderAPI_MakeFace(wire.Wire(), True).Face()
    return BRepPrimAPI_MakeRevol(face, gp_Ax1(point(0.0, 0.0), gp_Dir(*map(float, towards)))).Shape()


def cut_dishes(shape: TopoDS_Shape, dishes: list[Dish]) -> TopoDS_Shape | None:
    """Cut the recesses out of `shape`. One solid that is valid and still is once written and read
    back, or None. Runs in a child process."""
    tools = List_TopoDS_Shape()
    for dish in dishes:
        tools.Append(_tool(dish))
    arguments = List_TopoDS_Shape()
    arguments.Append(shape)
    operation = BRepAlgoAPI_Cut()
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.Build()
    if not operation.IsDone():
        return None
    solid = vh.largest_solid(operation.Shape())
    if solid is None or vh.volume(solid) <= 0 or not BRepCheck_Analyzer(solid).IsValid():
        return None
    # a revolved face can be valid here and broken in the file (practice part 4 after the rounds)
    return solid if export.reads_back_valid(solid) else None


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step: find the recesses, cut them one by one, keep what the verifier accepts."""
    with trace.step("cut_curved", kind="code",
                    description="Carries a curved face on under a flat ring the side views left around an axis") as step:
        if case_dir is None:
            return solid
        target = vh.read_volume(case_dir)
        if target and vh.volume(solid) < MIN_EXCESS * target:
            step.log("the solid already has the volume of the part")
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        observed = agent.evidence(case_dir, dims).observed
        before = score = None
        carved, kept, proposals = solid, 0, []
        for _ in range(MAX_PASSES):   # a cut can end at the next ring of the same recess
            dishes = find(carved, case_dir, observed, dims)
            proposals += [dish.summary() for dish in dishes]
            if not dishes or agent.out_of_time():
                break
            if score is None:
                before = score = verify.check(solid, case_dir, observed).score
            rings: dict[tuple, list[Dish]] = {}
            for dish in dishes:
                rings.setdefault(dish.ring, []).append(dish)
            cut_now = 0
            for alternatives in sorted(rings.values(), key=lambda group: -max(d.removes for d in group))[:MAX_RINGS]:
                best = None
                # most material first; what would leave less than the part's volume is not tried at all
                for dish in sorted(alternatives, key=lambda d: -d.removes):
                    if agent.out_of_time():
                        break
                    current = carved
                    if target and vh.volume(current) - dish.removes < MIN_SHARE * target:
                        continue
                    result = agent._guarded(step, current, target, lambda: agent.cutter.run(cut_dishes, current, [dish], TIMEOUT), quiet=True)
                    if result is None or vh.volume(result) >= vh.volume(current) * (1 - 1e-6):
                        continue
                    after = verify.check(result, case_dir, observed).score
                    if best is None or after > best[1]:
                        best = (result, after, dish)
                if best is None:
                    continue
                if best[1] < score - SCORE_SLACK:
                    step.log(f"the verifier rates the result lower ({score:.3f} to {best[1]:.3f}); not kept", level="warning")
                    continue
                carved, score, cut_now = best[0], best[1], cut_now + 1
                step.log(f"{best[2].view}: {best[2].how}, {best[2].removes:.0f} mm3")
            kept += cut_now
            if cut_now == 0:
                break
        step.metric(proposals=len(proposals))
        if proposals:
            step.attach("curved.json", proposals)
        if kept == 0:
            step.log("no flat ring of the solid is shown as curved" if not proposals else "no recess was cut")
            return solid
        step._status = None
        step.metric(cut=kept, score_before=round(before, 4), score_after=round(score, 4))
        agent._record(step, carved, "curved")
        return carved
