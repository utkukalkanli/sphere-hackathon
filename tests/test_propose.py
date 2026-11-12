"""Typed cuts (ops.py) and the step that applies proposed ones (propose.py), without a model."""

import json
from pathlib import Path

import pytest

from agentic_cad import evidence, flat, hull_agent, ops, propose
from agentic_cad import visual_hull as vh

CASE = Path(__file__).resolve().parents[1] / "artifacts/inputs/test_case_3"   # a flanged bushing, axis along x
BOX = {"x": 30.0, "y": 20.0, "z": 10.0}


def test_normalise_reads_what_a_model_writes():
    op = ops.normalise({"type": "Cyl", "p0": [0, 0, 0], "p1": {"x": 0, "y": 0, "z": 5}, "diameter": 4, "confidence": "80%"})
    assert op["type"] == "cylinder" and op["radius"] == 2 and op["confidence"] == pytest.approx(0.8)
    assert ops.normalise({"type": "box", "min": [5, 5, 5], "max": [0, 0, 0]})["min"] == [0, 0, 0]
    assert ops.confidence({"confidence": "high"}) == 0.9 and ops.confidence({"confidence": "?"}) == 0.5


@pytest.mark.parametrize("bad", [
    {"type": "sphere"}, {"type": "box", "min": [0, 0, 0], "max": [1, 1, 0]}, {"type": "box", "min": [0, 0, 0]},
    {"type": "cylinder", "p0": [0, 0, 0], "p1": [0, 0, 0], "radius": 1}, {"type": "prism", "axis": "W", "polygon": [], "range": [0, 1]},
    {"type": "region_floor", "view": "iso", "region": 1, "depth": 2}, {"type": "box", "min": [0, 0, "nan"], "max": [1, 1, 1]},
])
def test_normalise_refuses_malformed_records(bad):
    with pytest.raises((ValueError, KeyError, TypeError)):
        ops.normalise(bad)


@pytest.mark.parametrize("record, removed", [
    ({"type": "box", "min": [0, 0, 5], "max": [10, 20, 10]}, 1000.0),
    ({"type": "prism", "axis": "Z", "polygon": [[0, 0], [10, 0], [0, 10]], "range": [0, 10]}, 500.0),
    ({"type": "halfspace", "point": [20, 0, 0], "normal": [1, 0, 0]}, 2000.0),
    ({"type": "cylinder", "p0": [15, 10, 5], "p1": [15, 10, 10], "radius": 2}, 62.8318),
])
def test_cut_removes_the_volume_of_the_record(record, removed):
    box = vh.box(BOX)
    carved = ops.cut(box, ops.tool_for(ops.normalise(record)))
    assert vh.volume(box) - vh.volume(carved) == pytest.approx(removed, rel=1e-3)


def test_a_cut_that_splits_the_part_is_not_a_cut():
    assert ops.cut(vh.box(BOX), ops.normalise({"type": "box", "min": [10, -1, -1], "max": [20, 21, 11]})) is None


def test_region_floor_cuts_the_column_over_a_numbered_region():
    dims = dict(zip("xyz", vh.read_dimensions(CASE)))
    table = evidence.Evidence(CASE, dims)
    labels, items = table.regions()["right"]
    op = ops.normalise({"type": "region_floor", "view": "right", "region": 1, "depth": 2.0})
    box = vh.box(dims)
    carved = ops.cut(box, ops.tool_for(op, table))
    pixel = table.observed["right"].mm_per_px
    removed, column = vh.volume(box) - vh.volume(carved), items[0].area * pixel * pixel * 2.0
    assert column < removed < 1.2 * column   # the region and the two pixels to the middle of its lines
    assert ops.deeper(op, 0.5)["depth"] == 1.0
    with pytest.raises(ValueError):
        ops.tool_for({**op, "region": len(items) + 1}, table)


def test_parse_finds_the_ops_in_an_answer():
    ops_json = json.dumps({"ops": [{"type": "box"}]})
    assert propose.parse(f"Here you go:\n```json\n{ops_json}\n```\nanything else?") == [{"type": "box"}]
    assert propose.parse(ops_json) == [{"type": "box"}] and propose.parse('[{"type": "box"}, 3]') == [{"type": "box"}]
    assert propose.parse("no json here") == [] and propose.parse(None) == [] and propose.parse("{broken") == []


def test_without_an_endpoint_the_step_does_nothing(monkeypatch):
    for name in ("OPENAI_BASE_URL", "OPENAI_MODEL", "HULL_PROPOSALS"):
        monkeypatch.delenv(name, raising=False)
    solid = vh.box(BOX)
    assert not propose.enabled()
    assert propose.apply(hull_agent.VisualHullAgent(), solid, CASE, []) is solid


def _replay(tmp_path, monkeypatch):
    """Replayed proposals on the bounding box of practice 3: (box, result, agent, slab volume, output)."""
    dims = dict(zip("xyz", vh.read_dimensions(CASE)))
    x, y, z = dims["x"], dims["y"], dims["z"]
    proposals = {"ops": [
        {"id": "window", "type": "box", "min": [-1, 0.1 * y, 0.45 * z], "max": [x + 1, 0.2 * y, 0.55 * z], "confidence": 0.9},
        {"id": "corner", "type": "box", "min": [-1, -1, -1], "max": [x + 1, 0.04 * y, 0.04 * z], "confidence": 0.8},
        {"id": "nonsense", "type": "torus"},
    ]}
    (tmp_path / "proposals").mkdir()
    (tmp_path / "proposals" / "test_case_3.json").write_text(json.dumps(proposals))
    monkeypatch.setenv("HULL_PROPOSALS", str(tmp_path / "proposals"))
    monkeypatch.setattr(hull_agent, "LATER_STEPS", {"model": propose})
    monkeypatch.setattr(hull_agent, "SKIP_STEPS", set())
    output = tmp_path / "test_case_3.stp"
    agent = hull_agent.VisualHullAgent()
    agent.decisions = []
    box = vh.box(dims)
    try:
        result = agent.refine(box, CASE, [], output_path=output)
    finally:
        agent.close()
    corner = (x + 0) * 0.04 * y * 0.04 * z
    return box, result, agent, corner, output


def test_proposed_cuts_are_judged_one_by_one(tmp_path, monkeypatch):
    """A slab off the end of the box where the views show nothing is kept; a window through the
    flange is refused by the judge; what cannot be read is left out; and the kept cut is in the
    file. The one-colour face check is off here: it would refuse the window before the judge."""
    monkeypatch.setattr(flat, "ENABLED", False)
    box, result, agent, corner, output = _replay(tmp_path, monkeypatch)
    assert vh.volume(box) - vh.volume(result) == pytest.approx(corner, rel=1e-6)
    assert [d["accepted"] for d in agent.decisions if d["verdict"].mask > 0] == [False] * 2   # the window, tried twice
    assert output.is_file() and [p.name for p in tmp_path.iterdir() if p.is_file()] == ["test_case_3.stp"]


def test_one_colour_faces_refuse_the_window(tmp_path, monkeypatch):
    """With the one-colour face check on, the window through the flange draws its edges across
    faces the views show whole and is refused before the judge sees it; the slab is still kept."""
    box, result, agent, corner, output = _replay(tmp_path, monkeypatch)
    assert vh.volume(box) - vh.volume(result) == pytest.approx(corner, rel=1e-6)
    assert not [d for d in agent.decisions if d["verdict"].mask > 0]
