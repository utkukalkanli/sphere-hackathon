"""The source of outlines.py, the helper put into each agent's workspace (read by loop.py, never imported here).

outlines.py runs in the CadQuery environment of the agents' scripts: it imports nothing of agentic_cad.
"""

SOURCE = '''"""The part's outline in each pair of opposite straight views, measured from the renders (outlines.json).

    from outlines import silhouette, loops
    body = silhouette("front_back")                   # the front outline, through the whole box along Y
    body = body.intersect(silhouette("right_left"))   # ... and the side outline along X
    web = silhouette("top_bottom", 10.0, 22.0)        # the top outline only from Z = 10 to Z = 22

Names: "front_back" (a = X, b = Z, along Y), "right_left" (a = Y, b = Z, along X), "top_bottom" (a = X, b = Y,
along Z). Coordinates are millimetres in the frame of facts.json, (0, 0, 0) to the bounding box. The part lies
inside every silhouette: it is the outline, not the depth of what is inside it.
"""
import json
import os

import cadquery as cq
from cadquery import Edge, Face, Solid, Vector, Wire

_DATA = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "outlines.json")))
_AXIS = {"x": Vector(1, 0, 0), "y": Vector(0, 1, 0), "z": Vector(0, 0, 1)}


def loops(name):
    """The raw outlines of `name`: a list of {"outer": loop, "holes": [loop, ...]}. A loop is a list of
    ["line", [a, b], [a, b]], ["arc", start, middle, end] or ["circle", [a, b], radius], in mm."""
    return _DATA[name]["outlines"]


def _wire(spec, loop, at):
    u, v, w = _AXIS[spec["a"]], _AXIS[spec["b"]], _AXIS[spec["along"]]

    def p(ab):
        return u * float(ab[0]) + v * float(ab[1]) + w * float(at)

    edges = []
    for seg in loop:
        if seg[0] == "line":
            if (p(seg[1]) - p(seg[2])).Length > 1e-6:
                edges.append(Edge.makeLine(p(seg[1]), p(seg[2])))
        elif seg[0] == "arc":
            try:
                edges.append(Edge.makeThreePointArc(p(seg[1]), p(seg[2]), p(seg[3])))
            except Exception:
                edges.append(Edge.makeLine(p(seg[1]), p(seg[3])))
        else:
            return Wire.assembleEdges([Edge.makeCircle(float(seg[2]), p(seg[1]), w)])
    return Wire.assembleEdges(edges)


def silhouette(name, start=None, end=None):
    """The outline of `name` extruded along its axis from `start` to `end` (default: the whole box), holes
    through it cut away, as a cq.Workplane with one solid."""
    spec = _DATA[name]
    lo = 0.0 if start is None else float(start)
    hi = float(spec["depth"]) if end is None else float(end)
    step = _AXIS[spec["along"]] * (hi - lo)
    body = None
    for outline in spec["outlines"]:
        solid = Solid.extrudeLinear(Face.makeFromWires(_wire(spec, outline["outer"], lo)), step)
        for hole in outline.get("holes", []):
            solid = solid.cut(Solid.extrudeLinear(Face.makeFromWires(_wire(spec, hole, lo)), step))
        body = solid if body is None else body.fuse(solid)
    return cq.Workplane("XY").add(body.clean())
'''
