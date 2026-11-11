"""Camshaft retainer bracket: plate with 2 holes, front cylindrical recess, rear rib."""
import cadquery as cq
from common import finish

W, T, H = 19.0, 14.0, 50.8          # X, plate thickness (Y), Z
RIB = 5.0                            # rear rib projection in +Y
HOLE_D, HOLE_Z = 6.75, (6.35, 44.45)
RECESS_R, RIB_ARC_R = 12.7, 12.5

plate = cq.Workplane("XY").box(W, T, H, centered=False)

# rear rib: full-width band, trimmed top and bottom by R12.5 arcs centred on the hole axes
rib = cq.Workplane("XY").box(W, RIB, H, centered=False).translate((0, T, 0))
for z in HOLE_Z:
    cutter = (cq.Workplane("XZ").workplane(offset=-(T + RIB + 1)).center(W / 2, z)
              .circle(RIB_ARC_R).extrude(RIB + 2))
    rib = rib.cut(cutter)
body = plate.union(rib)

# front recess: R12.7 cylinder, axis along X, centred on the front face at mid height
recess = cq.Workplane("YZ").center(0, H / 2).circle(RECESS_R).extrude(W + 2).translate((-1, 0, 0))
body = body.cut(recess)

# two mounting holes through the plate along Y
for z in HOLE_Z:
    hole = cq.Workplane("XZ").workplane(offset=1).center(W / 2, z).circle(HOLE_D / 2).extrude(-(T + RIB + 2))
    body = body.cut(hole)

if __name__ == "__main__":
    finish("test_case_1", body)
