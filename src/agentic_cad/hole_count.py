"""Step, deterministic: give the solid the number of through-holes facts.json states.

`through_holes_genus` in facts.json is the genus of the part, counted from its B-rep topology. The
scorer counts ours the same way, and that count is a fifth of the score. Two things make ours differ:

- A pinch: two bodies of material that touch along one edge (more than two faces share it), or a
  face whose inner boundary touches its outer one in a point (a round boss cut tangent to a flat
  side). No hole is there, but the topology counts one. A hair of material is removed there, so
  the two no longer touch.
- A listed hole that is open in the part and closed in our solid, because the hull has material the
  part does not have beyond the hole's end. While the count is short, listed holes are made longer
  to where their axis leaves the material, the thinnest wall first, and a longer hole is kept only
  if the count rises and does not pass the stated one.

Nothing here can close a hole: a count above the stated one is left as it is.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Line
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_VERTEX, TopAbs_WIRE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape

from agentic_cad import holes as hl
from agentic_cad import spans
from agentic_cad import trace
from agentic_cad import visual_hull as vh
from agentic_cad.holes import Cylinder

ENABLED = os.environ.get("HULL_HOLE_COUNT", "1") != "0"
TIMEOUT = float(os.environ.get("HULL_HOLE_COUNT_TIMEOUT", 30))   # seconds for one cut
BUDGET = float(os.environ.get("HULL_HOLE_COUNT_BUDGET", 90))     # seconds for the step in one case
PINCH_RADIUS = 0.2   # mm of material taken away around a pinched edge
MARGIN = 0.05        # mm a lengthened hole goes on beyond the surface it comes out of
NUDGE = 0.05         # mm a hole's radius is changed by when the cut as listed pinches an edge
MAX_PINCHES = 20     # more than this is not a pinch here and there, the solid is something else


def genus(solid: TopoDS_Shape) -> int:
    """The number of through-holes, from the topology, as `through_holes_genus` counts them.
    Euler-Poincare: V - E + F - (L - F) = 2 * (shells - genus); degenerated edges do not count."""
    def mapped(kind):
        found = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(solid, kind, found)
        return found

    faces, edges, vertices, wires, shells = (mapped(kind) for kind in (
        TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX, TopAbs_WIRE, TopAbs_SHELL))
    real_edges = sum(0 if BRep_Tool.Degenerated_s(TopoDS.Edge(edges.FindKey(i))) else 1
                     for i in range(1, edges.Extent() + 1))
    f, v, l = faces.Extent(), vertices.Extent(), wires.Extent()
    return round(shells.Extent() - (v - real_edges + f - (l - f)) / 2.0)


def stated(case_dir: Path | None) -> int | None:
    """`through_holes_genus` of facts.json, or None when it is not there."""
    try:
        value = json.loads((case_dir / "facts.json").read_text()).get("through_holes_genus")
        return int(value) if value is not None and int(value) >= 0 else None
    except Exception:
        return None


def pinched(solid: TopoDS_Shape) -> list[tuple[np.ndarray, np.ndarray] | None]:
    """The edges more than two faces share: for a straight one its two ends, else None."""
    edges, faces = TopTools_IndexedMapOfShape(), TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(solid, TopAbs_EDGE, edges)
    TopExp.MapShapes_s(solid, TopAbs_FACE, faces)
    users = [0] * (edges.Extent() + 1)
    for i in range(1, faces.Extent() + 1):
        own = TopTools_IndexedMapOfShape()   # each edge of the face once, a seam too
        TopExp.MapShapes_s(faces.FindKey(i), TopAbs_EDGE, own)
        for k in range(1, own.Extent() + 1):
            users[edges.FindIndex(own.FindKey(k))] += 1
    found = []
    for i in range(1, edges.Extent() + 1):
        if users[i] <= 2:
            continue
        curve = BRepAdaptor_Curve(TopoDS.Edge(edges.FindKey(i)))
        if curve.GetType() != GeomAbs_Line:
            found.append(None)
            continue
        first, last = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        found.append((np.array([first.X(), first.Y(), first.Z()]), np.array([last.X(), last.Y(), last.Z()])))
    return found


def pinched_points(solid: TopoDS_Shape) -> list[np.ndarray]:
    """The points where two boundaries of one face touch: a vertex that two of its wires share."""
    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(solid, TopAbs_FACE, faces)
    found, seen = [], TopTools_IndexedMapOfShape()
    for i in range(1, faces.Extent() + 1):
        wires = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(faces.FindKey(i), TopAbs_WIRE, wires)
        if wires.Extent() < 2:
            continue
        met = TopTools_IndexedMapOfShape()
        for k in range(1, wires.Extent() + 1):
            own = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(wires.FindKey(k), TopAbs_VERTEX, own)
            for n in range(1, own.Extent() + 1):
                vertex = own.FindKey(n)
                if met.Contains(vertex) and not seen.Contains(vertex):
                    seen.Add(vertex)
                    point = BRep_Tool.Pnt_s(TopoDS.Vertex(vertex))
                    found.append(np.array([point.X(), point.Y(), point.Z()]))
                met.Add(vertex)
    return found


def _bite(point: np.ndarray) -> Cylinder:
    """The hair of material around a pinched point; askew, so that it lies in no face of the solid."""
    axis = np.array([0.48, 0.6, 0.64])
    return Cylinder(tuple(map(float, point)), tuple(map(float, axis / np.linalg.norm(axis))),
                    PINCH_RADIUS, 2 * PINCH_RADIUS)


def pinches(solid: TopoDS_Shape) -> int:
    return len(pinched(solid)) + len(pinched_points(solid))


def _relief(ends: tuple[np.ndarray, np.ndarray]) -> Cylinder | None:
    """The hair of material around a straight pinched edge, a little longer than the edge."""
    first, last = ends
    length = float(np.linalg.norm(last - first))
    if length < 1e-6:
        return None
    return Cylinder(tuple(map(float, (first + last) / 2)), tuple(map(float, (last - first) / length)),
                    PINCH_RADIUS, length + 2 * PINCH_RADIUS)


def longer(solid: TopoDS_Shape, holes: list[Cylinder]) -> list[tuple[float, Cylinder]]:
    """Every listed hole made longer, at one end or both, to where its axis leaves the material it
    ends in, and to where it leaves the solid altogether. With the length added, shortest first."""
    intersector = spans._intersector(solid)
    result = []
    for hole in holes:
        axis, centre, half = np.array(hole.axis, float), np.array(hole.centre, float), hole.length / 2
        probes = [centre] + list(spans._circle(centre, axis, 0.7 * hole.radius, 4))
        reach = {}
        for sign in (1, -1):
            wall = out = 0.0
            for probe in probes:
                stretches = spans._material(probe, sign * axis, intersector)
                if stretches is None:
                    wall = out = None
                    break
                beyond = [b - half for a, b in stretches if b > half + 1e-6]
                if not beyond:
                    continue
                inside = [b - half for a, b in stretches if a < half + 1e-6 < b]
                wall, out = max(wall, inside[0] if inside else 0.0), max(out, max(beyond))
            reach[sign] = (wall, out)
        margin = MARGIN + min(0.3 * hole.radius, 1.0)   # the surface it comes out of may be slanted
        for level in (0, 1):
            more = {sign: reach[sign][level] for sign in reach if reach[sign][level]}
            if level == 1:   # only where going on through the whole solid is more than the first wall
                more = {sign: value for sign, value in more.items() if value > reach[sign][0] + 1e-3}
            sides = [(sign,) for sign in more] + ([(1, -1)] if len(more) == 2 else [])
            for side in sides:
                plus = more[1] + margin if 1 in side else 0.0
                minus = more[-1] + margin if -1 in side else 0.0
                result.append((plus + minus, replace(
                    hole, centre=tuple(map(float, centre + axis * (plus - minus) / 2)),
                    length=float(hole.length + plus + minus), tip=0.0)))
    return sorted(result, key=lambda item: item[0])


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step."""
    with trace.step("hole_count", kind="code", description=(
            "Brings the number of through-holes to the one facts.json states")) as step:
        if not ENABLED or case_dir is None:
            return solid
        wanted, target = stated(case_dir), vh.read_volume(case_dir)
        # Counted on the solid with its same-surface faces merged, as the export writes it: that is
        # the one the scorer counts, and merging removes some pinches and uncovers others. Cut on the
        # solid as it is: a merged one fails more cuts.
        started, current = time.time(), solid
        count = genus(vh.tidy(current))
        step.metric(stated=wanted, before=count)

        def cut(base, cylinder):
            if time.time() - started > BUDGET or agent.out_of_time():
                return None
            carved = agent._guarded(step, base, target, lambda: agent.cutter.run(
                hl.cut_holes, base, [cylinder], TIMEOUT), quiet=True)
            return carved if carved is not None and vh.volume(carved) < vh.volume(base) else None

        def relieved(base):
            """`base` with a hair taken away at every pinch that can be: the solid, its count, the pinches left."""
            written = vh.tidy(base)
            left = pinches(written)
            if not 0 < left <= MAX_PINCHES:
                return base, genus(written), left
            reliefs = [_relief(ends) for ends in pinched(written) if ends is not None]
            reliefs = [cylinder for cylinder in reliefs if cylinder is not None]
            reliefs += [_bite(point) for point in pinched_points(written)]
            for cylinder in reliefs:
                carved = cut(base, cylinder)
                if carved is None:
                    continue
                written = vh.tidy(carved)
                if pinches(written) < left:
                    base, left = carved, pinches(written)
            return base, genus(vh.tidy(base)), left

        # 1. pinched edges and points. Whatever the count becomes: the one a pinch gives is not a
        # number of holes.
        found = pinches(vh.tidy(current))
        if found:
            current, count, left = relieved(current)
            step.metric(pinches=found, relieved=found - left)
            step.log(f"{found} pinched edges and points, {left} left; the count is now {count}")

        # 2. listed holes that should be open
        if wanted is not None and count < wanted:
            tried = opened = 0
            for added, cylinder in longer(current, hl.read_holes(case_dir)):
                if count >= wanted:
                    break
                # as listed first; a hole that only touches another surface pinches an edge there, and
                # a hair wider or narrower it may not
                for radius in (cylinder.radius, cylinder.radius + NUDGE, cylinder.radius - NUDGE):
                    carved = cut(current, replace(cylinder, radius=radius)) if radius > NUDGE else None
                    tried += 1
                    if carved is None:
                        continue
                    carved, after, left = relieved(carved)
                    if left or not count < after <= wanted:
                        continue
                    step.log(f"a hole of diameter {2 * radius:.2f} made {added:.2f} mm longer: "
                             f"{count} -> {after} through-holes")
                    current, count, opened = carved, after, opened + 1
                    break
            step.metric(tried=tried, opened=opened)
        elif wanted is not None and count > wanted:
            step.log(f"{count} through-holes where the part has {wanted}; nothing here closes one", level="warning")

        step.metric(after=count, seconds=round(time.time() - started, 1))
        if current is solid:
            return solid
        agent.lines_found = None   # the split step must look at this solid, not the one before
        agent._record(step, current, "hole_count")
        return current
