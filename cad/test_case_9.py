"""V6 engine block: X-shaped Y-Z section extruded along X, two 45 deg banks, hollow crankcase."""
import math
import cadquery as cq
from common import finish

LX, LY, LZ = 508.0, 255.94, 511.87
ZM = LZ / 2                              # mirror plane between the banks
S2 = math.sqrt(0.5)

# --- cross-section (Y, Z) -------------------------------------------------------------------
BASE_Y, BASE_HALF_Z = 176.0, 165.5       # central web: 0 <= y <= BASE_Y, |z - ZM| <= BASE_HALF_Z
APEX = (139.4, LZ)                       # top vertex of the upper bank (outer line meets head face)
HEAD_END = (LY, 395.33)                  # other end of the head face
SLOT_R = 63.5                            # crankshaft slot on the -Y face

# --- bank features (given for the upper bank; lower bank is the mirror, shifted in X) -------
BORE_D, BORE_X, BANK_DX = 127.0, [95.25, 241.3, 387.35], 25.4
HEAD_O = (197.665, 453.605)              # (y, z) where the bore axis meets the head face
BOLT_D, BOLT_DEPTH, BOLT_OFF = 9.52, 76.0, 55.88
BOLT_X = [22.61, 167.89, 314.71, 459.99]
PLATE_HALF_X, PLATE_HALF_W = 233.0, 82.4 # head plate footprint about the middle bore
PLATE_T = 12.7                           # plate stands proud of the bank end face
PLATE_CORNER = 25.4
RING_R, BOSS_R, RIM, POCKET_DEPTH = 76.2, 11.0, 8.0, 106.4   # water-jacket pockets round the liners

# --- crankcase pocket from the -Y face ------------------------------------------------------
WALL = 20.0
ROOF_Y, ROOF_CAP_Y = 174.36, 155.0       # roof planes are square to the bores; flat cap on top


def yz_prism(pts, x0, x1):
    return cq.Workplane("YZ").workplane(offset=x0).polyline(pts).close().extrude(x1 - x0)


# upper bank bar: head face, outer 45 deg face, then down into the base; inner 45 deg face
ay, az = APEX
hy, hz = HEAD_END
bar = [(ay, az), (hy, hz), (hy - (hz - ZM), ZM), (0, ZM), (0, az - ay)]
bar_lo = [(y, LZ - z) for y, z in bar]
base = [(0, ZM - BASE_HALF_Z), (BASE_Y, ZM - BASE_HALF_Z), (BASE_Y, ZM + BASE_HALF_Z), (0, ZM + BASE_HALF_Z)]
body = yz_prism(base, 0, LX).union(yz_prism(bar, 0, LX)).union(yz_prism(bar_lo, 0, LX))

# crankcase pocket and crank slot
h = BASE_HALF_Z - WALL
cap = ROOF_Y - ROOF_CAP_Y
pocket = [(-1, ZM - h), (ROOF_Y - h, ZM - h), (ROOF_CAP_Y, ZM - cap), (ROOF_CAP_Y, ZM + cap),
          (ROOF_Y - h, ZM + h), (-1, ZM + h)]
body = body.cut(yz_prism(pocket, WALL, LX - WALL))
body = body.cut(cq.Workplane("YZ").workplane(offset=-1).center(0, ZM).circle(SLOT_R).extrude(LX + 2))


def bank_cutters(dx):
    """Solids removed from the upper bank, built on a plane 1 mm above its head face."""
    wp = cq.Workplane(cq.Plane(origin=(dx, HEAD_O[0] + S2, HEAD_O[1] + S2), xDir=(1, 0, 0), normal=(0, S2, S2)))
    xc = BORE_X[1]
    bolts = [(x, s * BOLT_OFF) for x in BOLT_X for s in (-1, 1)]
    cutters = [wp.pushPoints([(x, 0)]).circle(BORE_D / 2).extrude(-171) for x in BORE_X]
    cutters += [wp.pushPoints([pt]).circle(BOLT_D / 2).extrude(-BOLT_DEPTH - 1) for pt in bolts]
    # bank end face outside the plate sits PLATE_T below the head face
    plate = (wp.center(xc, 0).rect(2 * PLATE_HALF_X, 2 * PLATE_HALF_W).extrude(-PLATE_T - 5)
             .edges("|(0,1,1)").fillet(PLATE_CORNER))
    cutters.append(wp.center(xc, 0).rect(1400, 2 * PLATE_HALF_W + 20).extrude(-PLATE_T - 1).cut(plate))
    # relief pockets: plate keeps a rim, a ring round each bore and a boss round each bolt
    relief = (wp.center(xc, 0).rect(2 * (PLATE_HALF_X - RIM), 2 * (PLATE_HALF_W - RIM)).extrude(-POCKET_DEPTH - 1)
              .edges("|(0,1,1)").fillet(PLATE_CORNER - RIM))
    for pt in [(x, 0) for x in BORE_X]:
        relief = relief.cut(wp.pushPoints([pt]).circle(RING_R).extrude(-POCKET_DEPTH - 3))
    for pt in bolts:
        relief = relief.cut(wp.pushPoints([pt]).circle(BOSS_R).extrude(-POCKET_DEPTH - 3))
    cutters.append(relief)
    return cutters


for c in bank_cutters(0):
    body = body.cut(c)
for c in bank_cutters(BANK_DX):
    body = body.cut(c.mirror("XY", (0, 0, ZM)))

if __name__ == "__main__":
    finish("test_case_9", body)
