"""Angled-bore mounting bracket (casting), approximated by its main masses:
top trough with lugs, Y-axis bored boss with domed end, barrel with stepped bore,
tilted front plate with bosses, and a hollow neck joining trough and barrel."""
import cadquery as cq
from common import finish

LX, LY, LZ = 162.25, 95.15, 152.74

# --- top trough (open-topped, curved floor) -------------------------------------------------
TR_X, TR_Y, TR_Z = (21.8, 149.7), (18.1, 73.4), (113.5, LZ)
TR_WALL = 6.0
SCOOP_R, SCOOP_C = 61.0, (85.65, 175.7)       # trough floor: cylinder, axis Y, (x, z) centre
FLOOR_T = 3.5
SIDE_LUGS = [(9.45, 32.05), (152.25, 32.05)]  # Ø9.5 holes, bosses full trough height
BACK_LUGS = [(43.65, 85.8), (113.95, 85.8)]   # Ø11 holes, 16.25 thick
LUG_R, BACK_LUG_Z0 = 9.45, 136.49
SIDE_LUG_R = [9.45, 10.0]                   # lug radius reaches the bounding box on each side

# --- bored boss along Y through the trough walls --------------------------------------------
BOSS_XZ = (84.95, 135.69)
FRONT_BOSS_R, DOME_R = 17.0, 15.75

# --- barrel (axis Y) and front plate --------------------------------------------------------
BAR_XZ, BAR_R, BAR_Y = (85.65, 64.39), 37.5, (21.25, 77.6)
PLATE_Y = (13.75, 21.25)
PLATE_PTS = [(40.9, 68.1), (56.1, 81.3), (71.7, 96.1), (71.7, 111.0), (110.5, 111.0), (119.6, 94.5),
             (124.5, 81.3), (143.1, 39.2), (141.8, 32.6), (131.9, 26.1), (117.1, 11.2), (95.6, 4.6),
             (86.6, 3.8), (79.2, 0.0), (71.7, 3.8), (39.9, 61.5)]
# plate bosses: (x, z, boss radius, back y, hole d)
PLATE_BOSSES = [(133.25, 37.69, 8.0, 33.75, 8.5), (52.75, 62.89, 8.5, 37.75, 8.5),
                (80.15, 11.24, 10.0, 28.75, 11.0)]

# --- neck between trough and barrel (hollow) ------------------------------------------------
NECK_Y, NECK_WALL = (21.25, 70.0), 3.0


def ycyl(r, x, z, y0, y1):
    return cq.Workplane("XZ").workplane(offset=-y0).center(x, z).circle(r).extrude(-(y1 - y0))


def zcyl(r, x, y, z0, z1):
    return cq.Workplane("XY").workplane(offset=z0).center(x, y).circle(r).extrude(z1 - z0)


def xz_prism(pts, y0, y1):
    return cq.Workplane("XZ").workplane(offset=-y0).polyline(pts).close().extrude(-(y1 - y0))


def box(x, y, z):
    return cq.Workplane("XY").box(x[1] - x[0], y[1] - y[0], z[1] - z[0], centered=False).translate((x[0], y[0], z[0]))


# trough: block, scooped out from the top, underside relieved so the floor is a curved shell
trough = box(TR_X, TR_Y, TR_Z).edges("|Z").fillet(7.0)
inner_y = (TR_Y[0] + TR_WALL, TR_Y[1] - TR_WALL)
scoop = ycyl(SCOOP_R, SCOOP_C[0], SCOOP_C[1], *inner_y).intersect(
    box((TR_X[0] + TR_WALL, TR_X[1] - TR_WALL), inner_y, (TR_Z[0], TR_Z[1] + 1)))
under = box((TR_X[0] - 1, TR_X[1] + 1), inner_y, (TR_Z[0] - 1, TR_Z[1] - 12)).cut(
    ycyl(SCOOP_R + FLOOR_T, SCOOP_C[0], SCOOP_C[1], inner_y[0] - 1, inner_y[1] + 1))
body = trough.cut(scoop).cut(under)

# lugs
for (x, y), r in zip(SIDE_LUGS, SIDE_LUG_R):
    edge = TR_X[0] if x < LX / 2 else TR_X[1]
    web_x = (min(x, edge), max(x, edge))
    body = body.union(zcyl(r, x, y, TR_Z[0], LZ)).union(box(web_x, (y - r, y + r), (TR_Z[0], LZ)))
for x, y in BACK_LUGS:
    body = body.union(zcyl(LUG_R, x, y, BACK_LUG_Z0, LZ)).union(
        box((x - LUG_R, x + LUG_R), (TR_Y[1] - 1, y), (BACK_LUG_Z0, LZ)))

# bored boss: front boss, back boss with domed end
bx, bz = BOSS_XZ
body = body.union(ycyl(FRONT_BOSS_R, bx, bz, 0, TR_Y[0] + 1))
body = body.union(ycyl(DOME_R, bx, bz, TR_Y[1] - TR_WALL, LY - DOME_R))
body = body.union(cq.Workplane("XY").sphere(DOME_R).translate((bx, LY - DOME_R, bz)))

# neck: outline seen from the front, hollow inside
neck_pts = (cq.Workplane("XZ").moveTo(30.5, 114.5).lineTo(140.2, 114.5)
            .threePointArc((127.0, 94.5), (124.5, 78.0)).lineTo(123.15, 64.4).lineTo(48.15, 64.4)
            .lineTo(46.2, 78.0).threePointArc((44.5, 94.5), (30.5, 114.5)).close())
neck = neck_pts.extrude(-(NECK_Y[1] - NECK_Y[0])).translate((0, NECK_Y[0], 0))
neck_void = (cq.Workplane("XZ").moveTo(39.0, 120.0).lineTo(132.0, 120.0)
             .threePointArc((122.5, 94.5), (120.0, 78.0)).lineTo(119.0, 64.4).lineTo(52.3, 64.4)
             .lineTo(50.7, 78.0).threePointArc((49.0, 94.5), (39.0, 120.0)).close()
             .extrude(-(NECK_Y[1] - NECK_Y[0] - 2 * NECK_WALL)).translate((0, NECK_Y[0] + NECK_WALL, 0)))
body = body.union(neck.cut(neck_void))

# barrel and front plate with bosses
cx, cz = BAR_XZ
body = body.union(ycyl(BAR_R, cx, cz, *BAR_Y))
body = body.union(xz_prism(PLATE_PTS, *PLATE_Y))
for x, z, r, y1, _ in PLATE_BOSSES:
    body = body.union(ycyl(r, x, z, PLATE_Y[0], y1))
body = body.union(ycyl(7.0, 104.55, 92.09, PLATE_Y[0], 26.75))

# --- bores and holes ------------------------------------------------------------------------
body = body.cut(ycyl(60.0 / 2, cx, cz, 33.55, 67.55)).cut(ycyl(65.0 / 2, cx, cz, 67.55, LY))
body = body.cut(ycyl(29.0 / 2, cx, cz, 21.25, 33.6)).cut(ycyl(18.0 / 2, cx, cz, PLATE_Y[0] - 1, 21.3))
body = body.cut(ycyl(17.5 / 2, bx, bz, 8.9, 84.0)).cut(ycyl(26.0 / 2, bx, bz, -1, 9.0))
for x, z, r, y1, d in PLATE_BOSSES:
    body = body.cut(ycyl(d / 2, x, z, PLATE_Y[0] - 1, y1 + 1))
body = body.cut(ycyl(13.0 / 2, 80.15, 11.24, PLATE_Y[0] - 1, 17.75))
body = body.cut(ycyl(9.0 / 2, 104.55, 92.09, PLATE_Y[0] - 1, 27.75)).cut(
    ycyl(13.0 / 2, 104.55, 92.09, PLATE_Y[0] - 1, 17.75))
for x, y in SIDE_LUGS:
    body = body.cut(zcyl(9.5 / 2, x, y, TR_Z[0] - 1, LZ + 1))
for x, y in BACK_LUGS:
    body = body.cut(zcyl(11.0 / 2, x, y, BACK_LUG_Z0 - 1, LZ + 1))

if __name__ == "__main__":
    finish("test_case_7", body)
