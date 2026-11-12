"""The local accept rule (judge.py) on a practice part: what it must refuse whatever the tones say."""

from pathlib import Path

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from agentic_cad import judge, verify
from agentic_cad import visual_hull as vh

CASE = Path(__file__).resolve().parents[1] / "artifacts/inputs/test_case_3"


@pytest.fixture(scope="module")
def setup():
    dims = dict(zip("xyz", vh.read_dimensions(CASE)))
    return dims, judge.Judge(CASE, dims, verify.observe(CASE, dims)), vh.box(dims)


def without(solid, low, high):
    cut = BRepAlgoAPI_Cut(solid, BRepPrimAPI_MakeBox(gp_Pnt(*low), gp_Pnt(*high)).Shape())
    return vh.largest_solid(cut.Shape())


def test_the_same_solid_changes_nothing(setup):
    dims, rater, box = setup
    verdict = rater.rate(box, box)
    assert verdict.accepted and verdict.changed == 0 and verdict.error == 0


def test_a_cut_through_the_part_breaks_the_outline(setup):
    """Practice 3 is a flanged bushing with its axis along x: a window through the box where the
    flange is shows as a hole in the two silhouettes along x."""
    dims, rater, box = setup
    y = dims["y"]
    gap = without(box, (-1, 0.1 * y, 0.45 * dims["z"]), (dims["x"] + 1, 0.2 * y, 0.55 * dims["z"]))
    verdict = rater.rate(box, gap)
    assert not verdict.accepted and verdict.mask > 0


def test_less_than_the_parts_volume_is_refused_unseen(setup):
    dims, rater, box = setup
    sliver = without(box, (-1, -1, -1), (dims["x"] + 1, dims["y"] + 1, 0.999 * dims["z"]))
    verdict = rater.rate(box, sliver)
    assert not verdict.accepted and "volume" in verdict.reason
