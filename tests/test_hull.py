"""Tests for the deterministic stages of the hull engine. No model endpoint is needed."""

import json
import math
import time
from pathlib import Path

import cadquery as cq
import numpy as np
import pytest

from agentic_cad import cadlib, hull, pipeline, render
from agentic_cad.config import Settings
from agentic_cad.runner import tessellate
from agentic_cad.trace import Tracer

FILES = {"front": "view_0_front.png", "right": "view_3_right.png", "top": "view_4_top.png"}


def bracket() -> cq.Workplane:
    """L-bracket 60 x 30 x 40 with a blind bore along X in the upright and a through hole along Z in the foot."""
    body = cadlib.box(0, 0, 0, 60, 30, 10).union(cadlib.box(0, 0, 0, 12, 30, 40))
    body = body.cut(cadlib.cyl("x", 8, -1, 8, 15, 28))
    return body.cut(cadlib.cyl("z", 10, -1, 11, 40, 15))


@pytest.fixture(scope="module")
def case_dir(tmp_path_factory) -> Path:
    """A test case in the layout of artifacts/inputs, rendered from the bracket."""
    root = tmp_path_factory.mktemp("case") / "test_case_1"
    (root / "images").mkdir(parents=True)
    solid = bracket().val()
    verts, tris = tessellate(solid)
    for view in render.VIEWS:
        if view[0] in FILES:
            render.shaded_tile(verts, tris, view, 768).save(root / "images" / FILES[view[0]])
    (root / "facts.json").write_text(json.dumps({
        "bounding_box_mm": {"x": 60.0, "y": 30.0, "z": 40.0}, "volume_mm3": solid.Volume(),
        "surface_area_mm2": solid.Area(),
        "cylindrical_holes_and_bores": [
            {"diameter": 8.0, "axis": "X", "count": 1, "lengths": [8.0], "centres": [[4.0, 15.0, 28.0]]}]}))
    (root / "description.md").write_text("# Bracket\n")
    return root


def run(script: str, base=None) -> cq.Workplane:
    namespace = {"cq": cq, "math": math, **cadlib.HELPERS}
    if base is not None:
        namespace["base"] = base
    exec(script, namespace)
    return namespace["result"]


def test_three_view_hull_matches_the_part(case_dir):
    box = {"x": 60.0, "y": 30.0, "z": 40.0}
    views = {name: case_dir / "images" / file for name, file in FILES.items()}
    outlines = hull.view_outlines(views, box)
    assert set(outlines) == {"front", "right", "top"}
    assert len(outlines["top"][0][1]) == 1          # the Z hole is a hole of the top outline
    label, script = hull.hull_candidates(outlines, box)[0]
    assert label == "front+right+top"
    solid = run(script).val()
    truth = bracket().val()
    assert len(solid.Solids()) == 1 and solid.isValid()
    bb = solid.BoundingBox()
    assert [bb.xlen, bb.ylen, bb.zlen] == pytest.approx([60, 30, 40], abs=0.05)
    union = solid.fuse(truth).Volume()
    assert (solid.Volume() + truth.Volume() - union) / union > 0.95


def test_candidates_fall_back_to_fewer_views():
    outlines = {"front": [([(0, 0), (10, 0), (10, 5), (0, 5)], [])]}
    candidates = hull.hull_candidates(outlines, {"x": 10, "y": 4, "z": 5})
    assert [label for label, _ in candidates] == ["front"]
    assert run(candidates[0][1]).val().Volume() == pytest.approx(200.0)
    assert hull.hull_candidates({}, {"x": 10, "y": 4, "z": 5}) == []
    assert hull.mask_polygons(np.zeros((32, 32), dtype=bool), 10, 10) == []


def test_bore_cuts_place_cylinders_from_the_facts():
    facts = {"cylindrical_holes_and_bores": [
        {"diameter": 6.75, "axis": "Y", "count": 2, "lengths": [14.0], "centres": [[9.5, 7.0, 6.35], [9.5, 7.0, 44.45]]},
        {"diameter": 4.5, "axis": "(0.00, 0.60, 0.80)", "count": 1, "lengths": [10.0], "centres": [[5.0, 5.0, 5.0]]},
        {"diameter": 3.0, "axis": "?", "count": 1, "lengths": [1.0], "centres": [[0, 0, 0]]}]}
    cuts = hull.bore_cuts(facts)
    assert [expr for _, expr in cuts[:2]] == ["cyl('y', 6.75, 0, 14, 9.5, 6.35)", "cyl('y', 6.75, 0, 14, 9.5, 44.45)"]
    assert cuts[2][1] == "cyl_between(4.5, (5.0, 2.0, 1.0), (5.0, 8.0, 9.0))"
    assert len(cuts) == 3


def test_try_cut_skips_cuts_that_break_the_part():
    body = cadlib.box(0, 0, 0, 30, 10, 10)
    assert cadlib.try_cut(body, cadlib.box(10, -1, -1, 20, 11, 11)) is body          # would split it in two
    assert cadlib.try_cut(body, cadlib.box(40, 0, 0, 50, 10, 10)) is body            # removes nothing
    assert cadlib.try_cut(body, cadlib.box(10, -1, 5, 20, 11, 11), min_volume=2900) is body
    assert cadlib.try_cut(body, cadlib.box(10, -1, 5, 20, 11, 11)).val().Volume() == pytest.approx(2500.0)


def test_deterministic_stages_write_a_hull_with_the_located_bore(case_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ENGINE", "hull")
    monkeypatch.setenv("AGENT_LLM_STAGE", "0")
    monkeypatch.setenv("AGENT_FEEDBACK_IMAGES", "0")
    monkeypatch.delenv("TRACE", raising=False)
    output = tmp_path / "out" / "test_case_1.stp"
    summary = pipeline.run_case(Settings.from_env(), "test_case_1", case_dir, "# Bracket\n",
                                sorted((case_dir / "images").glob("view_*.png")), None, output,
                                time.time() + 120, Tracer().case("test_case_1"))
    assert summary["written"] and summary["status"] == "ok"
    assert summary["stage"] == "hull+bores"          # no outline shows the blind X bore
    assert abs(summary["volume_err"]) < 0.03
    solid = cq.importers.importStep(str(output)).val()
    assert len(solid.Solids()) == 1 and solid.isValid()
