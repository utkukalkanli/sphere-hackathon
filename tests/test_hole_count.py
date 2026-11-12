"""The hole_count step: the through-holes of the solid counted as facts.json counts them, pinched
edges relieved, listed holes opened until the stated number is reached."""

import json
import math

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from agentic_cad import hole_count
from agentic_cad import hull_agent
from agentic_cad import visual_hull as vh

BLOCK = {"x": 30.0, "y": 20.0, "z": 10.0}


def drilled(solid, x: float, y: float, from_z: float, depth: float, radius: float = 2.0):
    """`solid` with a hole along z, from `from_z` upwards."""
    tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, from_z), gp_Dir(0, 0, 1)), radius, depth).Shape()
    return vh.largest_solid(BRepAlgoAPI_Cut(solid, tool).Shape())


def case(tmp_path, holes: int, entries: list[dict], volume: float):
    (tmp_path / "facts.json").write_text(json.dumps({
        "bounding_box_mm": BLOCK, "volume_mm3": volume, "through_holes_genus": holes,
        "cylindrical_holes_and_bores": entries}))
    return tmp_path


@pytest.fixture
def agent():
    agent = hull_agent.VisualHullAgent()
    yield agent
    agent.close()


def test_genus_counts_through_holes_only():
    block = vh.box(BLOCK)
    assert hole_count.genus(block) == 0
    assert hole_count.genus(drilled(block, 8, 10, 5, 10)) == 0       # blind: open at the top only
    through = drilled(block, 8, 10, -1, 12)
    assert hole_count.genus(through) == 1
    assert hole_count.genus(drilled(through, 22, 10, -1, 12)) == 2


def test_two_windows_that_meet_in_an_edge_pinch_it_and_the_step_relieves_it(tmp_path, agent):
    block = vh.box(BLOCK)
    for corner in ((5.0, 2.0), (13.0, 10.0)):   # the windows share the edge x = 13, y = 10
        window = BRepPrimAPI_MakeBox(gp_Pnt(*corner, -1.0), 8.0, 8.0, 12.0).Shape()
        block = vh.largest_solid(BRepAlgoAPI_Cut(block, window).Shape())
    assert len(hole_count.pinched(block)) == 1
    result = hole_count.apply(agent, block, case(tmp_path, 1, [], 0.9 * vh.volume(block)), [])
    assert hole_count.pinched(result) == []
    assert hole_count.genus(result) == 1   # the two windows are one opening now
    assert vh.volume(result) == pytest.approx(vh.volume(block), rel=1e-3)


def test_a_listed_hole_is_opened_when_the_count_is_short(tmp_path, agent):
    """The part has a hole right through; ours stops halfway, as its listed length says."""
    ours = drilled(vh.box(BLOCK), 15, 10, 5, 10)
    true_volume = 6000.0 - math.pi * 4 * 10
    entry = {"diameter": 4.0, "axis": "Z", "lengths": [5.0], "centres": [[15.0, 10.0, 7.5]]}
    result = hole_count.apply(agent, ours, case(tmp_path, 1, [entry], true_volume), [])
    assert hole_count.genus(result) == 1
    assert vh.volume(result) == pytest.approx(true_volume, rel=0.01)


def test_nothing_is_opened_when_the_count_is_right(tmp_path, agent):
    ours = drilled(vh.box(BLOCK), 15, 10, 5, 10)
    entry = {"diameter": 4.0, "axis": "Z", "lengths": [5.0], "centres": [[15.0, 10.0, 7.5]]}
    assert hole_count.apply(agent, ours, case(tmp_path, 0, [entry], 0.9 * vh.volume(ours)), []) is ours


def test_only_as_many_holes_are_opened_as_are_missing(tmp_path, agent):
    ours = vh.box(BLOCK)
    for x in (6, 15, 24):
        ours = drilled(ours, x, 10, 5, 10)
    entry = {"diameter": 4.0, "axis": "Z", "lengths": [5.0], "centres": [[x, 10.0, 7.5] for x in (6.0, 15.0, 24.0)]}
    result = hole_count.apply(agent, ours, case(tmp_path, 2, [entry], 0.9 * vh.volume(ours)), [])
    assert hole_count.genus(result) == 2

