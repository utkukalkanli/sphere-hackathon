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


HELPERS = {f.__name__: f for f in (box, cyl, tube, cone, cyl_between, sphere, prism, revolve, arc_points,
                                   polar_points, try_fillet, try_chamfer)}

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
"""
