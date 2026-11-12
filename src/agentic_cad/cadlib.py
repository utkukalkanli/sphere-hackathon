"""Absolute-coordinate CadQuery helpers that are injected into every generated script.

They exist so the model can place features straight from the brief ("hole d=6.75 along Y at
x=9.5, z=6.35") without workplane gymnastics. Everything returns a ``cq.Workplane`` holding one
solid, so ``.union() / .cut() / .intersect() / .translate() / .fillet()`` work as usual.
"""

from __future__ import annotations

import math

import cadquery as cq

_AXIS = {"x": 0, "y": 1, "z": 2}
_PLANE_NORMAL = {"xy": "z", "xz": "y", "yz": "x"}


def _wp(solid) -> cq.Workplane:
    return cq.Workplane(obj=solid)


def _vec(axis: str, a: float, c1: float, c2: float) -> cq.Vector:
    """Point with coordinate `a` along `axis` and (c1, c2) on the two other axes, in x<y<z order."""
    i = _AXIS[axis.lower()]
    others = [k for k in range(3) if k != i]
    v = [0.0, 0.0, 0.0]
    v[i], v[others[0]], v[others[1]] = a, c1, c2
    return cq.Vector(*v)


def box(x0, y0, z0, x1, y1, z1) -> cq.Workplane:
    """Axis-aligned box between two opposite corners."""
    lo = cq.Vector(min(x0, x1), min(y0, y1), min(z0, z1))
    return _wp(cq.Solid.makeBox(abs(x1 - x0), abs(y1 - y0), abs(z1 - z0), lo))


def cyl(axis, d, a0, a1, c1, c2) -> cq.Workplane:
    """Cylinder of diameter d along `axis` from a0 to a1; (c1, c2) = its centre on the other two
    axes in x<y<z order, i.e. axis 'x' -> (y, z), axis 'y' -> (x, z), axis 'z' -> (x, y)."""
    a0, a1 = sorted((a0, a1))
    return _wp(cq.Solid.makeCylinder(d / 2, a1 - a0, _vec(axis, a0, c1, c2), _vec(axis, 1, 0, 0)))


def tube(axis, d_outer, d_inner, a0, a1, c1, c2) -> cq.Workplane:
    """Hollow cylinder (ring / sleeve)."""
    return cyl(axis, d_outer, a0, a1, c1, c2).cut(cyl(axis, d_inner, a0 - 1, a1 + 1, c1, c2))


def cone(axis, d0, d1, a0, a1, c1, c2) -> cq.Workplane:
    """Cone frustum along `axis`: diameter d0 at a0, d1 at a1 (either may be 0)."""
    if a0 > a1:
        a0, a1, d0, d1 = a1, a0, d1, d0
    return _wp(cq.Solid.makeCone(max(d0, 1e-6) / 2, max(d1, 1e-6) / 2, a1 - a0,
                                 _vec(axis, a0, c1, c2), _vec(axis, 1, 0, 0)))


def cyl_between(d, p0, p1) -> cq.Workplane:
    """Cylinder of diameter d from point p0 to point p1 (any direction), e.g. an inclined bore."""
    a, b = cq.Vector(*p0), cq.Vector(*p1)
    return _wp(cq.Solid.makeCylinder(d / 2, (b - a).Length, a, b - a))


def cone_between(d0, d1, p0, p1) -> cq.Workplane:
    """Cone frustum from point p0 (diameter d0) to point p1 (diameter d1), any direction."""
    a, b = cq.Vector(*p0), cq.Vector(*p1)
    return _wp(cq.Solid.makeCone(max(d0, 1e-6) / 2, max(d1, 1e-6) / 2, (b - a).Length, a, b - a))


def tube_between(d_outer, d_inner, p0, p1) -> cq.Workplane:
    """Hollow cylinder from point p0 to point p1 (any direction): the material between the two diameters."""
    a, b = cq.Vector(*p0), cq.Vector(*p1)
    step = (b - a).normalized()
    return cyl_between(d_outer, p0, p1).cut(cyl_between(d_inner, (a - step).toTuple(), (b + step).toTuple()))


def sphere(d, x, y, z) -> cq.Workplane:
    return cq.Workplane("XY").sphere(d / 2).translate((x, y, z))


def prism(plane, pts, a0, a1) -> cq.Workplane:
    """Extrude a closed polygon. `plane` is 'xy', 'xz' or 'yz'; pts are 2D points in that plane's
    own coordinates (for 'xz': (x, z)); the solid spans a0..a1 along the remaining axis."""
    plane = plane.lower()
    normal = _PLANE_NORMAL[plane]
    a0, a1 = sorted((a0, a1))
    pts3 = [_vec(normal, a0, p[0], p[1]) for p in pts]
    face = cq.Face.makeFromWires(cq.Wire.makePolygon(pts3, close=True))
    return _wp(cq.Solid.extrudeLinear(face, _vec(normal, a1 - a0, 0, 0)))


def revolve(axis, profile, c1=0.0, c2=0.0) -> cq.Workplane:
    """Solid of revolution about the line parallel to `axis` through (c1, c2).
    profile = closed polygon of (position_along_axis, radius) points, radius >= 0."""
    pts3 = [_vec(axis, a, c1 + max(r, 0.0), c2) for a, r in profile]
    face = cq.Face.makeFromWires(cq.Wire.makePolygon(pts3, close=True))
    return _wp(cq.Solid.revolve(face, 360.0, _vec(axis, 0, c1, c2), _vec(axis, 1, c1, c2)))


def arc_points(cx, cy, r, start_deg, end_deg, n=24):
    """Points along a circular arc: use them inside prism()/revolve() polygons for round outlines."""
    return [(cx + r * math.cos(math.radians(start_deg + (end_deg - start_deg) * i / n)),
             cy + r * math.sin(math.radians(start_deg + (end_deg - start_deg) * i / n))) for i in range(n + 1)]


def polar_points(n, r, cx=0.0, cy=0.0, start_deg=0.0):
    """n points equally spaced on a circle (bolt circles and patterns)."""
    return [(cx + r * math.cos(math.radians(start_deg + 360.0 * i / n)),
             cy + r * math.sin(math.radians(start_deg + 360.0 * i / n))) for i in range(n)]


def try_fillet(body, radius, selector=None) -> cq.Workplane:
    """Fillet the selected edges (all edges if selector is None); returns `body` unchanged if the
    kernel cannot build the fillet, so a cosmetic round never breaks the part."""
    try:
        edges = body.edges(selector) if selector else body.edges()
        out = edges.fillet(radius)
        return out if out.val().isValid() else body
    except Exception:
        return body


def try_chamfer(body, length, selector=None) -> cq.Workplane:
    try:
        edges = body.edges(selector) if selector else body.edges()
        out = edges.chamfer(length)
        return out if out.val().isValid() else body
    except Exception:
        return body


def through_holes(solid) -> int:
    """Number of through-holes (genus) of a solid, from its B-rep topology: the same count as
    `through_holes_genus` in facts.json. Euler-Poincare: V - E + F - (L - F) = 2 * (shells - genus)."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX, TopAbs_WIRE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    def mapped(kind):
        found = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(solid.wrapped, kind, found)
        return found

    faces, edges, verts, wires = (mapped(k) for k in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX, TopAbs_WIRE))
    n_edges = sum(0 if BRep_Tool.Degenerated_s(TopoDS.Edge_s(edges.FindKey(i))) else 1
                  for i in range(1, edges.Extent() + 1))
    F, V, L = faces.Extent(), verts.Extent(), wires.Extent()
    return round(len(solid.Shells()) - (V - n_edges + F - (L - F)) / 2.0)


def try_cut(body, cutter, min_volume=0.0, max_holes=None) -> cq.Workplane:
    """body.cut(cutter), unless that fails, removes nothing, splits the part into several solids, leaves
    less than min_volume mm3 or gives the part more than max_holes through-holes: then `body` is returned
    unchanged, so one bad cut never ruins the part."""
    try:
        out = body.cut(cutter)
        solids = out.val().Solids()
        before, after = body.val().Volume(), out.val().Volume()
        if len(solids) == 1 and out.val().isValid() and min_volume <= after < before - 1e-6:
            if max_holes is None or through_holes(solids[0]) <= max(max_holes, through_holes(body.val().Solids()[0])):
                return out
    except Exception:
        pass
    return body


def _cut_outcome(body, cutter, min_volume=0.0, max_holes=None):
    """(solid after the cut, mm3 removed, through-holes after, None) when the cut is acceptable, else
    (None, 0, None, reason). Raises when the kernel cannot build the cut."""
    out = body.cut(cutter)
    solids = out.val().Solids()
    before, after = body.val().Volume(), out.val().Volume()
    if after >= before - 1e-6:
        return None, 0.0, None, "it removes nothing (it lies outside the part or in material that is already gone)"
    if len(solids) != 1 or not out.val().isValid():
        return None, 0.0, None, "it would split the part into pieces"
    if after < min_volume:
        return None, 0.0, None, (f"it would leave {after:.0f} mm3, less than the part's volume: it is too large "
                                 "or in the wrong place")
    holes = through_holes(solids[0])
    if max_holes is not None and holes > max(max_holes, through_holes(body.val().Solids()[0])):
        return None, 0.0, None, "it would open more through-holes than the part has: it must stay blind"
    return out, before - after, holes, None


def cut_feature(body, make_cutter, label, log, min_volume=0.0, max_holes=None) -> cq.Workplane:
    """try_cut for one feature of a feature list (features.py), with the outcome appended to `log`: the cut is
    skipped, and the reason recorded, when the cutter cannot be built or the cut would break the part."""
    try:
        out, removed, _, reason = _cut_outcome(body, make_cutter(), min_volume, max_holes)
    except Exception as exc:
        log.append(f"{label} -> skipped: the geometry could not be built ({type(exc).__name__})")
        return body
    if out is None:
        log.append(f"{label} -> skipped: {reason}")
        return body
    log.append(f"{label} -> cut, removed {removed:.0f} mm3")
    return out


def _material_span(solid, p0, p1):
    """(t0, t1) along the line p0 -> p1 (0 at p0, mm) of the material the segment p0..p1 lies in or touches: from
    where the line enters the solid to where it leaves it. None when the segment is not in material."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    a, b = cq.Vector(*p0), cq.Vector(*p1)
    length, step = (b - a).Length, (b - a).normalized()
    probe = IntCurvesFace_ShapeIntersector()
    probe.Load(solid.wrapped, 1e-6)
    probe.Perform(gp_Lin(gp_Pnt(*a.toTuple()), gp_Dir(*step.toTuple())), -1e7, 1e7)
    crossings = sorted(probe.WParameter(i) for i in range(1, probe.NbPnt() + 1))
    lo = hi = None
    for t0, t1 in zip(crossings, crossings[1:]):
        if t1 - t0 < 1e-6 or t1 < -1e-6 or t0 > length + 1e-6:
            continue
        if solid.isInside(a + step * ((t0 + t1) / 2)):
            lo, hi = (t0 if lo is None else min(lo, t0)), (t1 if hi is None else max(hi, t1))
    return None if lo is None else (lo, hi)


def cut_through(body, d, p0, p1, label, log, min_volume=0.0, max_holes=None) -> cq.Workplane:
    """A round hole that has to go through (features.py, "through": true). The hole is tried as given, then
    lengthened to where its axis leaves the material it is in, then through the whole part; the shortest one
    that opens a new through-hole and passes the checks of cut_feature is kept. If none does, the hole is cut
    as given, so a wrong "through" costs nothing."""
    a, b = cq.Vector(*p0), cq.Vector(*p1)
    step = (b - a).normalized()
    solid = body.val().Solids()[0]
    tries = [(a, b, "as given")]
    try:
        span = _material_span(solid, p0, p1)
    except Exception:
        span = None
    if span is not None:
        for margin in (max(0.5, 0.05 * d), 0.5 * d + 0.5):
            lo, hi = min(span[0] - margin, 0.0), max(span[1] + margin, (b - a).Length)
            tries.append((a + step * lo, a + step * hi, f"lengthened to {hi - lo:.1f} mm"))
    reach = body.val().BoundingBox().DiagonalLength
    tries.append((a - step * reach, b + step * reach, "taken through the whole part"))
    before = through_holes(solid)
    for start, end, how in tries:
        try:
            out, removed, holes, _ = _cut_outcome(body, cyl_between(d, start.toTuple(), end.toTuple()), min_volume,
                                                  max_holes)
        except Exception:
            continue
        if out is not None and holes > before:
            log.append(f"{label} -> cut through ({how}), removed {removed:.0f} mm3")
            return out
    return cut_feature(body, lambda: cyl_between(d, p0, p1), label + " (no length opened a through-hole)", log,
                       min_volume, max_holes)


def cut_closed(body, d, p0, p1, label, log, min_volume=0.0, max_holes=None, depth_in_d=1.25) -> cq.Workplane:
    """A round hole that the reference views show closed when looking along it. If the cylinder p0 -> p1 covers
    all the material on its axis it would be a straight passage, which the views rule out: such a hole was
    drilled from the faces, so it is cut blind from both ends, `depth_in_d` diameters deep. Otherwise the
    hole is cut as given."""
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

    a, b = cq.Vector(*p0), cq.Vector(*p1)
    length, step = (b - a).Length, (b - a).normalized()
    first = last = None
    try:
        solid = body.val().Solids()[0]
        probe = IntCurvesFace_ShapeIntersector()
        probe.Load(solid.wrapped, 1e-6)
        probe.Perform(gp_Lin(gp_Pnt(*a.toTuple()), gp_Dir(*step.toTuple())), -1e7, 1e7)
        crossings = sorted(probe.WParameter(i) for i in range(1, probe.NbPnt() + 1))
        inside = [(t0, t1) for t0, t1 in zip(crossings, crossings[1:])
                  if t1 - t0 > 1e-6 and solid.isInside(a + step * ((t0 + t1) / 2))]
        if inside:
            first, last = inside[0][0], inside[-1][1]
    except Exception:
        pass
    if first is None or first < -0.05 or last > length + 0.05:      # the hole ends inside material: blind as given
        return cut_feature(body, lambda: cyl_between(d, p0, p1), label, log, min_volume, max_holes)
    depth = min(depth_in_d * d, 0.4 * (last - first))
    for t0, t1, end in ((first - 1.0, first + depth, "near end"), (last - depth, last + 1.0, "far end")):
        s, e = (a + step * t0).toTuple(), (a + step * t1).toTuple()
        body = cut_feature(body, lambda s=s, e=e: cyl_between(d, s, e),
                           f"{label} (closed in the views: drilled {depth:.1f} deep from the {end})", log,
                           min_volume, max_holes)
    return body


HELPERS = {f.__name__: f for f in (box, cyl, tube, cone, cyl_between, cone_between, tube_between, sphere, prism,
                                   revolve, arc_points, polar_points, try_fillet, try_chamfer, try_cut, cut_feature,
                                   cut_through, cut_closed)}

HELPER_DOC = """\
box(x0, y0, z0, x1, y1, z1)                 axis-aligned box between two corners
cyl(axis, d, a0, a1, c1, c2)                cylinder, diameter d, along axis 'x'|'y'|'z' from a0 to a1;
                                            (c1, c2) = centre on the other two axes in x<y<z order:
                                            axis 'x' -> (y, z); axis 'y' -> (x, z); axis 'z' -> (x, y)
tube(axis, d_outer, d_inner, a0, a1, c1, c2) hollow cylinder
cone(axis, d0, d1, a0, a1, c1, c2)          frustum: diameter d0 at a0, d1 at a1 (same centre rule)
cyl_between(d, (x0,y0,z0), (x1,y1,z1))      cylinder between two points (inclined bores)
sphere(d, x, y, z)
prism(plane, pts, a0, a1)                   extrude closed polygon; plane 'xy'|'xz'|'yz', pts in that
                                            plane's coords (for 'xz': (x, z)), spanning a0..a1 on the 3rd axis
revolve(axis, profile, c1, c2)              revolve closed polygon of (position_along_axis, radius) points
                                            360 deg about the line parallel to axis through (c1, c2)
arc_points(cx, cy, r, start_deg, end_deg, n=24)   polygon points along an arc (for prism/revolve outlines)
polar_points(n, r, cx, cy, start_deg=0)     n points on a circle (hole patterns)
try_fillet(body, radius, selector=None)     fillet that silently skips if the kernel fails
try_chamfer(body, length, selector=None)
try_cut(body, cutter, min_volume=0, max_holes=None)   body.cut(cutter), skipped if it fails, splits the part, removes
                                            nothing or leaves more than max_holes through-holes
"""
