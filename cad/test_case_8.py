"""Hydraulic pump main body: figure-eight housing wall, bolt lugs, side port pads, two feet."""
import math
import cadquery as cq
from common import finish

LX, LY, H = 80.0, 128.0, 40.2
CX, CY = 40.0, 72.95
BORE_R = 44.45 / 2                    # the two overlapping window bores
BORE_DY = 18.85                       # bore centres at CY +/- this
WAIST_HALF = 15.0                     # flat waist of the window
WALL_R = 30.0                         # outer wall arcs, concentric with the bores
PAD_X = (7.0, 73.0)                   # side port pad faces
BOLT_D, LUG_R = 7.0, 6.0
BOLTS_LUG = [(40.0, 122.0), (18.79, 113.21), (61.21, 113.21), (40.0, 23.9)]
BOLTS_FULL = [(18.79, 32.69), (61.21, 32.69)]
BOLTS_PAD = [(16.0, 72.95), (64.0, 72.95)]
MAIN_BORE_D, CROSS_D = 20.0, 4.2
CROSS_YZ = [(85.45, 32.6), (85.45, 7.6), (60.45, 7.6), (60.45, 32.6)]
FOOT_HOLE_D = 6.0


def zcyl(r, x, y, z0=0.0, z1=H):
    return cq.Workplane("XY").workplane(offset=z0).center(x, y).circle(r).extrude(z1 - z0)


def xcyl(d, y, z):
    return cq.Workplane("YZ").center(y, z).circle(d / 2).extrude(LX)


# --- housing wall + side pads ---------------------------------------------------------------
body = zcyl(WALL_R, CX, CY - BORE_DY).union(zcyl(WALL_R, CX, CY + BORE_DY))
pad = (cq.Workplane("XY").center(CX, CY).rect(PAD_X[1] - PAD_X[0], 2 * BORE_DY).extrude(H))
body = body.union(pad)

# --- bolt bosses: R6 lobes centred on the wall's outer surface, full height -------------------
for x, y in BOLTS_LUG + BOLTS_FULL:
    body = body.union(zcyl(LUG_R, x, y))

# --- feet at the -Y end (mirror pair) -------------------------------------------------------
FOOT_ARC_R, FOOT_ARC_Y = 14.6, 22.6
foot = (cq.Workplane("XY").moveTo(0, 0).lineTo(18.6, 0).lineTo(26.5, 8.06).lineTo(26.5, 30.0)
        .lineTo(FOOT_ARC_R, 30.0).lineTo(FOOT_ARC_R, FOOT_ARC_Y)
        .threePointArc((FOOT_ARC_R * math.sin(math.pi / 4), FOOT_ARC_Y - FOOT_ARC_R * math.cos(math.pi / 4)),
                       (0, FOOT_ARC_Y - FOOT_ARC_R))
        .close().extrude(H))
body = body.union(foot).union(foot.mirror("YZ", (CX, 0, 0)))

# --- openings -------------------------------------------------------------------------------
window = (zcyl(BORE_R, CX, CY - BORE_DY, -1, H + 1).union(zcyl(BORE_R, CX, CY + BORE_DY, -1, H + 1))
          .union(cq.Workplane("XY").workplane(offset=-1).center(CX, CY).rect(2 * WAIST_HALF, 5.4).extrude(H + 2)))
body = body.cut(window)
for x, y in BOLTS_LUG + BOLTS_FULL + BOLTS_PAD:
    body = body.cut(zcyl(BOLT_D / 2, x, y, -1, H + 1))
body = body.cut(xcyl(MAIN_BORE_D, CY, H / 2))
for y, z in CROSS_YZ:
    body = body.cut(xcyl(CROSS_D, y, z))
for x in (4.62, LX - 4.62):
    for z in (9.0, 31.2):
        hole = cq.Workplane("XZ").workplane(offset=1).center(x, z).circle(FOOT_HOLE_D / 2).extrude(-12)
        body = body.cut(hole)

if __name__ == "__main__":
    finish("test_case_8", body)
