"""Engine poppet valve: body of revolution about Y (head disc + stem)."""
import math
import cadquery as cq
from common import finish

HEAD_R, RIM_T = 53.34 / 2, 1.27
RIM_CHAMFER = 2.54                  # 45 deg chamfer on the back rim edge
STEM_R, STEM_START, STEM_LEN = 6.36 / 2, 17.955, 66.35
TOTAL = 85.55
FILLET_R = 15.25                    # stem-root blend, tangent to the stem

y_back = RIM_T + RIM_CHAMFER                      # inner edge of the rim chamfer
stem_end = STEM_START + STEM_LEN
tip_ch = TOTAL - stem_end                         # 45 deg tip chamfer
# concave dish: one R15.25 arc, tangent to the stem at its root, dipping below the
# chamfer edge and rising back to meet it (centre at r = STEM_R + R, y = STEM_START)
cr = STEM_R + FILLET_R
dish_bottom = (cr, STEM_START - FILLET_R)

# profile in (radius, y); revolved about the Y axis
profile = (cq.Workplane("XY")
           .moveTo(0, 0).lineTo(HEAD_R, 0).lineTo(HEAD_R, RIM_T)
           .lineTo(HEAD_R - RIM_CHAMFER, y_back)
           .threePointArc(dish_bottom, (STEM_R, STEM_START))
           .lineTo(STEM_R, stem_end).lineTo(STEM_R - tip_ch, TOTAL).lineTo(0, TOTAL).close())
body = profile.revolve(360, (0, 0, 0), (0, 1, 0)).translate((HEAD_R, 0, HEAD_R))

if __name__ == "__main__":
    finish("test_case_2", body)
