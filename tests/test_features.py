"""Tests for the feature schema and its compiler. No model endpoint is needed."""

import math

import cadquery as cq
import pytest

from agentic_cad import cadlib, features


def build(script: str, base: cq.Workplane):
    namespace = {"cq": cq, "math": math, "base": base, **cadlib.HELPERS}
    exec(script, namespace)
    return namespace["result"], namespace["feature_log"]


def test_every_feature_type_compiles_and_cuts():
    base = cadlib.box(0, 0, 0, 100, 60, 40)
    wanted = [
        {"label": "pocket", "type": "box", "min": [10, 10, 30], "max": [30, 30, 41]},
        {"label": "blind bore", "type": "cylinder", "diameter": 10, "start": [50, 30, 41], "end": [50, 30, 20],
         "through": False},
        {"label": "countersink", "type": "cone", "diameter_start": 16, "diameter_end": 0, "start": [70, 15, 41],
         "end": [70, 15, 33]},
        {"label": "groove", "type": "ring", "outer_diameter": 20, "inner_diameter": 14, "start": [80, 40, 41],
         "end": [80, 40, 35]},
        {"label": "slot", "type": "prism", "plane": "xz", "points": [[40, 0], [60, 0], [50, 8]], "from": -1, "to": 10},
        {"label": "stepped bore", "type": "revolve", "axis": "x", "centre": [30, 15],
         "profile": [[-1, 0], [-1, 6], [10, 6], [10, 3], [20, 3], [20, 0]]},
    ]
    assert all(features.FEATURES[f["type"]]["required"] == list(f) for f in wanted)   # the examples fit the schema
    result, log = build(features.compile_script(wanted), base)
    assert len(log) == len(wanted) and all("-> cut" in line for line in log)
    assert len(result.val().Solids()) == 1 and result.val().Volume() < base.val().Volume()
    box = result.val().BoundingBox()
    assert (box.xlen, box.ylen, box.zlen) == pytest.approx((100, 60, 40))


def test_bad_features_are_skipped_with_a_reason_and_do_not_spoil_the_rest():
    base = cadlib.box(0, 0, 0, 100, 60, 40)
    wanted = [
        {"label": "splits the part", "type": "box", "min": [45, -1, -1], "max": [55, 61, 41]},
        {"label": "outside", "type": "box", "min": [200, 0, 0], "max": [210, 10, 10]},
        {"label": "goes through", "type": "cylinder", "diameter": 8, "start": [20, 30, -1], "end": [20, 30, 41],
         "through": False},
        {"label": "no length", "type": "cylinder", "diameter": 8, "start": [1, 1, 1], "end": [1, 1, 1],
         "through": False},
        {"label": "unknown", "type": "sweep"},
        {"label": "too much", "type": "box", "min": [-1, -1, 5], "max": [101, 61, 41]},
        {"label": "fine", "type": "box", "min": [70, 20, 30], "max": [90, 40, 41]},
    ]
    result, log = build(features.compile_script(wanted, min_volume=200000, max_holes=0), base)
    assert ["split" in log[0], "removes nothing" in log[1], "through-holes" in log[2], "rejected" in log[3],
            "rejected" in log[4], "less than the part's volume" in log[5], "-> cut" in log[6]] == [True] * 7
    assert result.val().Volume() == pytest.approx(240000 - 20 * 20 * 10)


def test_parse_reads_the_object_and_tolerates_a_fence():
    reply = '```json\n{"plan": "p", "features": [{"label": "a", "type": "box", "min": [0, 0, 0], "max": [1, 1, 1]}]}\n```'
    assert features.parse(reply)[0]["type"] == "box"
    assert features.parse('{"plan": "nothing hidden", "features": []}') == []
    assert features.parse("no json here") is None


def test_through_cylinder_is_lengthened_until_it_opens_a_hole():
    # a plate with a taller block behind it: the hole is through the 10 mm plate only
    base = cadlib.box(0, 0, 0, 100, 60, 10).union(cadlib.box(0, 40, 0, 100, 60, 40))
    short = {"label": "plate hole, 2 mm short", "type": "cylinder", "diameter": 8, "start": [20, 20, 10],
             "end": [20, 20, 2]}
    blind, _ = build(features.compile_script([{**short, "through": False}], max_holes=1), base)
    assert cadlib.through_holes(blind.val()) == 0
    result, log = build(features.compile_script([{**short, "through": True}], max_holes=1), base)
    assert cadlib.through_holes(result.val()) == 1 and "cut through (lengthened" in log[0]
    # only the plate's 10 mm were drilled, not the whole part
    assert base.val().Volume() - result.val().Volume() == pytest.approx(math.pi * 16 * 10, rel=0.01)


def test_through_cylinder_stays_as_given_when_the_part_has_no_hole_left_to_open():
    base = cadlib.box(0, 0, 0, 100, 60, 10)
    wanted = [{"label": "hole", "type": "cylinder", "diameter": 8, "start": [20, 20, 10], "end": [20, 20, 4],
               "through": True}]
    result, log = build(features.compile_script(wanted, max_holes=0), base)
    assert cadlib.through_holes(result.val()) == 0 and "no length opened a through-hole" in log[0]
    assert base.val().Volume() - result.val().Volume() == pytest.approx(math.pi * 16 * 6, rel=0.01)


def test_repeat_makes_rows_and_circles_and_checks_every_copy():
    base = cadlib.box(0, 0, 0, 100, 100, 10)
    row = {"label": "row", "type": "cylinder", "diameter": 6, "start": [10, 10, -1], "end": [10, 10, 11],
           "through": True, "repeat": {"count": 4, "step": [20, 0, 0]}}
    circle = {"label": "pockets", "type": "box", "min": [76, 48, 6], "max": [80, 52, 11],
              "repeat": {"count": 6, "around": "z", "centre": [50, 50]}}
    result, log = build(features.compile_script([row, circle], max_holes=3), base)
    assert cadlib.through_holes(result.val()) == 3          # the fourth copy would exceed the part's count
    assert [("cut" in line, "copy" in line) for line in log[:3]] == [(True, True)] * 3 and "skipped" in log[3]
    assert sum("-> cut, removed" in line for line in log[4:]) == 6
    assert base.val().Volume() - result.val().Volume() == pytest.approx(3 * math.pi * 9 * 10 + 6 * 4 * 4 * 4, rel=0.01)


def test_bad_repeat_rejects_the_feature():
    base = cadlib.box(0, 0, 0, 100, 60, 40)
    wanted = [{"label": "a", "type": "box", "min": [1, 1, 30], "max": [5, 5, 41], "repeat": {"count": 1, "step": [9, 0, 0]}},
              {"label": "b", "type": "box", "min": [1, 1, 30], "max": [5, 5, 41], "repeat": {"count": 3, "step": [0, 0, 0]}},
              {"label": "c", "type": "box", "min": [1, 1, 30], "max": [5, 5, 41], "repeat": {"count": 3}}]
    _, log = build(features.compile_script(wanted), base)
    assert len(log) == 3 and all("rejected" in line for line in log)
