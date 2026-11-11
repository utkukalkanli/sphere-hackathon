"""Conical sunburst-finned air-filter element: flange + thin 18-pleat star frustum + domed cap."""
import math
import cadquery as cq
from common import finish

FLANGE_R, CAP_R = 70.0, 36.5
EDGE_R, BAND = 10.0, 7.5              # outer round and cylindrical band of flange / cap
H = 136.5
Z_FLANGE_TOP = EDGE_R + BAND          # 17.5
Z_CAP_BOTTOM = H - EDGE_R - BAND      # 119.0
N_FINS = 18
VALLEY_RATIO = 26.5 / 36.5            # groove-bottom radius / fin-tip radius (same apex)
WALL = 0.668                            # pleat sheet thickness (the pleated cone is hollow)
BORE_D, BORE_DEPTH = 30.0, 17.5


def star(r_tip):
    pts = []
    for k in range(N_FINS):
        a_tip = math.radians(90 + k * 360 / N_FINS)
        a_val = a_tip + math.radians(180 / N_FINS)
        pts.append((r_tip * math.cos(a_tip), r_tip * math.sin(a_tip)))
        pts.append((r_tip * VALLEY_RATIO * math.cos(a_val), r_tip * VALLEY_RATIO * math.sin(a_val)))
    return pts


flange = cq.Workplane("XY").circle(FLANGE_R).extrude(Z_FLANGE_TOP).faces("<Z").edges().fillet(EDGE_R)
cap = (cq.Workplane("XY").workplane(offset=Z_CAP_BOTTOM).circle(CAP_R).extrude(H - Z_CAP_BOTTOM)
       .faces(">Z").edges().fillet(EDGE_R))
def star_frustum(inset):
    wp = cq.Workplane("XY").workplane(offset=Z_FLANGE_TOP).polyline(star(FLANGE_R)).close()
    if inset:
        wp = wp.offset2D(-inset, "intersection")
    wp = wp.workplane(offset=Z_CAP_BOTTOM - Z_FLANGE_TOP).polyline(star(CAP_R)).close()
    if inset:
        wp = wp.offset2D(-inset, "intersection")
    return wp.loft(ruled=True, combine=True)


fins = star_frustum(0).cut(star_frustum(WALL))
body = flange.union(fins).union(cap)
# the bore runs through the flange plate and opens into the hollow pleated cone
body = body.cut(cq.Workplane("XY").circle(BORE_D / 2).extrude(BORE_DEPTH + 0.5))
body = body.translate((FLANGE_R, FLANGE_R, 0))

if __name__ == "__main__":
    finish("test_case_6", body)
