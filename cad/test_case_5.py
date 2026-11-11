"""Engine cylinder head: rounded block with tabs, combustion recesses, valve pockets, ports, plug wells."""
import math
import cadquery as cq
from common import finish

LX, LY, LZ = 508.0, 85.73, 165.1
TAB_Y = 22.23                         # tabs project this far in front of the body
Y0, Y1 = TAB_Y, LY                    # body front / back faces
ZC = 82.55                            # mid height
PITCH = 146.05
CHAMBER_X = [120.35 + i * PITCH for i in range(3)]
VALVE_DX = 30.48
VALVE_X = [c + s * VALVE_DX for c in CHAMBER_X for s in (-1, 1)]
TAB_X = [47.6 + i * PITCH for i in range(4)]
TAB_W, TAB_H, TAB_ZC = 19.05, 50.8, 130.18
NOTCH_R = 12.7

RECESS_D, RECESS_DEPTH = 127.0, 2.54
POCKET_R, POCKET_DEPTH = 27.0, 34.5   # domed valve pocket below the recess floor
PORT_W, PORT_Y = 32.5, (34.93, 73.03) # rounded-rect port, X width and Y range
PORT_CORNER = 6.0
PORT_REACH = 7.0                      # how far the port runs past the valve axis
EXH_PORT_DX = -12.2                   # bottom ports sit offset from their valve
SEAT_D, SEAT_DEPTH, GUIDE_D = 28.0, 7.62, 6.35
SMALL_D, SMALL_DEPTH = 5.11, 16.51
PLUG_D = 12.7
PLUG_PT, PLUG_ANG = (81.02, 43.04), math.radians(25.0)   # (y, z) on the plug axis, tilt from Y
WELL_R0, WELL_R1 = 20.0, PLUG_D / 2   # well radius at the front face / at the plug hole


def ycyl(d, x, z, y0, y1):
    return cq.Workplane("XZ").workplane(offset=-y0).center(x, z).circle(d / 2).extrude(-(y1 - y0))


# --- body with rounded X ends, and the four tabs --------------------------------------------
body = (cq.Workplane("XZ").workplane(offset=-Y0).rect(LX, LZ, centered=False).extrude(-(Y1 - Y0))
        .edges("|Y").fillet(25.4))
for x in TAB_X:
    tab = cq.Workplane("XY").box(TAB_W, TAB_Y + 1, TAB_H).translate((x, (TAB_Y + 1) / 2, TAB_ZC))
    body = body.union(tab)
# long transverse bore with its axis in the tab front plane -> semicircular notch in each tab
notch = cq.Workplane("YZ").center(0, TAB_ZC).circle(NOTCH_R).extrude(LX)
body = body.cut(notch)

# --- back face: combustion recesses, domed valve pockets ------------------------------------
floor_y = Y1 - RECESS_DEPTH
for x in CHAMBER_X:
    body = body.cut(ycyl(RECESS_D, x, ZC, floor_y, Y1 + 1))
n = 16
ell = [(POCKET_R * math.cos(t), -POCKET_DEPTH * math.sin(t))
       for t in [i * math.pi / 2 / n for i in range(n + 1)]]
dome = (cq.Workplane("XY").moveTo(0, 0).lineTo(*ell[0]).spline(ell[1:], includeCurrent=True)
        .close().revolve(360, (0, 0, 0), (0, 1, 0)))
for x in VALVE_X:
    body = body.cut(dome.translate((x, floor_y, ZC)))

# --- ports: intake from the top face, exhaust from the bottom face --------------------------
def port(x, z0, z1):
    return (cq.Workplane("XY").workplane(offset=z0)
            .center(x, (PORT_Y[0] + PORT_Y[1]) / 2).rect(PORT_W, PORT_Y[1] - PORT_Y[0])
            .extrude(z1 - z0).edges("|Z").fillet(PORT_CORNER))

for i, x in enumerate(VALVE_X):
    if i % 2:      # higher-x valve of each pair: port from the top
        body = body.cut(port(x, ZC - PORT_REACH, LZ + 1))
    else:          # lower-x valve: port from the bottom, offset in X
        body = body.cut(port(x + EXH_PORT_DX, -1, ZC + PORT_REACH))

# --- front face: valve seats, guides, small holes -------------------------------------------
for x in VALVE_X:
    body = body.cut(ycyl(SEAT_D, x, ZC, Y0 - 1, Y0 + SEAT_DEPTH))
    body = body.cut(ycyl(GUIDE_D, x, ZC, Y0, floor_y - POCKET_DEPTH + 2))
    body = body.cut(ycyl(SMALL_D, x, 102.87, Y0 - 1, Y0 + SMALL_DEPTH))
for x in TAB_X:
    for z in (149.23, 111.13):
        body = body.cut(ycyl(SMALL_D, x, z, -1, SMALL_DEPTH))

# --- spark-plug wells: tapered, tilted 25 deg in Y-Z, opening on the front/bottom edge ------
ax = cq.Vector(0, math.cos(PLUG_ANG), math.sin(PLUG_ANG))
z_front = PLUG_PT[1] - (PLUG_PT[0] - Y0) * math.tan(PLUG_ANG)
s_end = (PLUG_PT[0] - PLUG_D * 0.42 - Y0) / math.cos(PLUG_ANG)      # where the plug hole starts
slope = (WELL_R0 - WELL_R1) / s_end
s_out = -45.0
for cx in CHAMBER_X:
    x = cx + 0.3
    p0 = cq.Vector(x, Y0, z_front)
    well = cq.Solid.makeCone(WELL_R0 - slope * s_out, WELL_R1, s_end - s_out, p0 + ax * s_out, ax)
    plug = cq.Solid.makeCylinder(PLUG_D / 2, 30, p0 + ax * (s_end - 1), ax)
    body = body.cut(cq.Workplane(obj=well)).cut(cq.Workplane(obj=plug))

if __name__ == "__main__":
    finish("test_case_5", body)
