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
        {"label": "blind bore", "type": "cylinder", "diameter": 10, "start": [50, 30, 41], "end": [50, 30, 20]},
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
        {"label": "goes through", "type": "cylinder", "diameter": 8, "start": [20, 30, -1], "end": [20, 30, 41]},
        {"label": "no length", "type": "cylinder", "diameter": 8, "start": [1, 1, 1], "end": [1, 1, 1]},
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
