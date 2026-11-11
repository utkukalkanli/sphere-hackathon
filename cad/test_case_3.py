"""Crankshaft bushing: flanged sleeve, axis X."""
import cadquery as cq
from common import finish

BORE, OD, FLANGE_OD = 76.2, 127.0, 132.08
L_SLEEVE, L_FLANGE = 19.05, 2.54
C = FLANGE_OD / 2

body = (cq.Workplane("YZ").center(C, C).circle(OD / 2).extrude(L_SLEEVE)
        .faces(">X").workplane().circle(FLANGE_OD / 2).extrude(L_FLANGE)
        .faces(">X").workplane().circle(BORE / 2).cutThruAll())

if __name__ == "__main__":
    finish("test_case_3", body)
