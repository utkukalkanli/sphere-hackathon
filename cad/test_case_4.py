"""Three-throw crankshaft, axis Z. Each throw = crank pin between two web plates."""
import math
import cadquery as cq
from common import finish

L = 564.88
AXIS = (105.79, 114.3)               # rotation axis in X, Y
SHAFT_R = 76.2 / 2                   # journals (and crank pins)
THROW = 63.5                         # pin offset from the axis
HUB_R, PIN_END_R = 88.9 / 2, 101.6 / 2   # web outline: arc round the axis / round the pin
EDGE_R = 2.54                        # rounds on the web edges
GAP = 54.62                          # clear pin length between the two webs of a throw
SPAN = 87.54 + 2 * EDGE_R            # outer span of a web pair
WEB_T = (SPAN - GAP) / 2
RELIEF_R, RELIEF_START = 169.0, 19.05    # convex relief on the inner web face, hub end
# (z centre, throw direction angle in the XY plane)
THROWS = [(136.48, -90.0), (282.44, 150.0), (428.4, 30.0)]


def cyl(r, z0, z1, c=(0, 0)):
    return cq.Workplane("XY").workplane(offset=z0).center(*c).circle(r).extrude(z1 - z0)


def web(inner_up):
    """Web plate in a local frame: axis at the origin, pin towards +X, z in [0, WEB_T]."""
    cphi = (HUB_R - PIN_END_R) / THROW
    sphi = math.sqrt(1 - cphi ** 2)
    h = (HUB_R * cphi, HUB_R * sphi)
    p = (THROW + PIN_END_R * cphi, PIN_END_R * sphi)
    plate = (cq.Workplane("XY").moveTo(*h).lineTo(*p).threePointArc((THROW + PIN_END_R, 0), (p[0], -p[1]))
             .lineTo(h[0], -h[1]).threePointArc((-HUB_R, 0), h).close().extrude(WEB_T)
             .faces("|Z").edges().fillet(EDGE_R))
    # relief: inner face rolls off along an R169 cylinder (axis along local Y) towards the hub end
    z_face = WEB_T if inner_up else 0.0
    zc = z_face - RELIEF_R if inner_up else z_face + RELIEF_R
    keep = (cq.Workplane("XZ").workplane(offset=-100).center(RELIEF_START, zc)
            .circle(RELIEF_R).extrude(200))
    block = cq.Workplane("XY").box(100, 200, WEB_T + 2, centered=(False, True, False)).translate(
        (RELIEF_START - 100, 0, -1))
    return plate.cut(block.cut(keep))


def throw(zc, angle):
    lo, hi = zc - SPAN / 2, zc + SPAN / 2
    t = web(True).translate((0, 0, lo)).union(web(False).translate((0, 0, hi - WEB_T)))
    t = t.union(cyl(SHAFT_R, lo + WEB_T / 2, hi - WEB_T / 2, (THROW, 0)))      # crank pin
    return t.rotate((0, 0, 0), (0, 0, 1), angle)


body = None
z_prev = 0.0
for zc, ang in THROWS:
    journal = cyl(SHAFT_R, z_prev, zc - SPAN / 2 + WEB_T / 2)
    body = journal if body is None else body.union(journal)
    body = body.union(throw(zc, ang))
    z_prev = zc + SPAN / 2 - WEB_T / 2
body = body.union(cyl(SHAFT_R, z_prev, L)).translate((AXIS[0], AXIS[1], 0))

if __name__ == "__main__":
    finish("test_case_4", body)
