"""Tests for the flow engine: the brief as the only source of facts, the see-through test and the graph."""

import json
import time
from pathlib import Path

import cadquery as cq
import pytest

from agentic_cad import cadlib, flow, hull, render
from agentic_cad.config import Settings
from agentic_cad.evaluate import facts_from_description
from agentic_cad.runner import tessellate
from agentic_cad.trace import Tracer

FILES = {"front": "view_0_front.png", "back": "view_1_back.png", "left": "view_2_left.png",
         "right": "view_3_right.png", "top": "view_4_top.png", "bottom": "view_5_bottom.png"}
BOX = {"x": 60.0, "y": 30.0, "z": 40.0}


def bracket() -> cq.Workplane:
    """L-bracket 60 x 30 x 40: a blind bore along X in the upright, a through hole along Z in the foot."""
    body = cadlib.box(0, 0, 0, 60, 30, 10).union(cadlib.box(0, 0, 0, 12, 30, 40))
    body = body.cut(cadlib.cyl("x", 8, -1, 8, 15, 28))
    return body.cut(cadlib.cyl("z", 10, -1, 11, 40, 15))


BRIEF = """# Bracket

**Envelope:** 60 x 30 x 40 mm (X x Y x Z); volume {volume:.2f} cm^3; 1 through‑passages.

## Features
- Blind bore d = 8 mm, axis X, 8 mm deep from the X = 0 face at (Y = 15, Z = 28).
- Through hole d = 10 mm, axis Z, through the 10 mm foot at (X = 40, Y = 15).
"""


@pytest.fixture(scope="module")
def case_dir(tmp_path_factory) -> Path:
    """A hidden-style case: views and a brief, no facts.json."""
    root = tmp_path_factory.mktemp("case") / "test_case_1"
    (root / "images").mkdir(parents=True)
    solid = bracket().val()
    verts, tris = tessellate(solid)
    for view in render.VIEWS:
        if view[0] in FILES:
            render.shaded_tile(verts, tris, view, 768).save(root / "images" / FILES[view[0]])
    (root / "description.md").write_text(BRIEF.format(volume=solid.Volume() / 1000))
    return root


def views_of(case_dir: Path) -> dict[str, Path]:
    return {name: case_dir / "images" / file for name, file in FILES.items()}


def test_the_brief_gives_envelope_volume_and_hole_count():
    facts = facts_from_description("**Envelope:** 80 x 128 x 40.2 mm (X x Y x Z); volume 121.46 cm^3; "
                                   "11 through‑passages.")
    assert facts["bounding_box_mm"] == {"x": 80.0, "y": 128.0, "z": 40.2}
    assert facts["volume_mm3"] == pytest.approx(121460.0)
    assert facts["through_holes_genus"] == 11
    assert facts_from_description("volume 3 cm3; no through-holes")["through_holes_genus"] == 0


def test_see_through_tells_a_through_hole_from_a_blind_one(case_dir):
    views = views_of(case_dir)
    assert hull.see_through(views, BOX, "z", (40, 15, 5), 10) is True
    assert hull.see_through(views, BOX, "x", (4, 15, 28), 8) is False
    assert hull.see_through(views, BOX, "z", (5, 5, 5), 4) is False          # solid material
    assert hull.see_through({}, BOX, "z", (40, 15, 5), 10) is None


def test_hole_features_follow_the_views_not_the_wording(case_dir):
    holes = [{"label": "foot hole", "diameter": 10.0, "axis": "z", "centre": [40, 15, 5], "length": 10.0, "through": False},
             {"label": "bore", "diameter": 8.0, "axis": "x", "centre": [30, 15, 28], "length": 60.0, "through": True}]
    holes.append({"label": "one bore for a wider opening", "diameter": 14.0, "axis": "z", "centre": [40, 15, 5],
                  "length": 10.0, "through": True})
    cuts = flow.hole_features(holes, views_of(case_dir), BOX)
    assert len(cuts) == 2                       # daylight in the middle only: left to the hull's outline
    assert cuts[0]["through"] is True and cuts[0]["start"][2] < 0 and cuts[0]["end"][2] > 10 - 1e-9
    assert cuts[1]["closed"] is True and cuts[1]["start"][0] == -1.0 and cuts[1]["end"][0] == 61.0


def test_a_hole_closed_in_the_views_is_drilled_from_both_ends():
    block = cadlib.box(0, 0, 0, 60, 30, 40)
    log: list[str] = []
    out = cadlib.cut_closed(block, 8, (-1, 15, 20), (61, 15, 20), "bore", log)     # spans all the material
    assert cadlib.through_holes(out.val().Solids()[0]) == 0 and len(log) == 2
    assert out.val().Volume() == pytest.approx(block.val().Volume() - 2 * 3.14159265 * 16 * 10, rel=1e-3)
    out = cadlib.cut_closed(block, 8, (-1, 15, 20), (12, 15, 20), "bore", log)     # ends in material: as given
    assert out.val().Volume() == pytest.approx(block.val().Volume() - 3.14159265 * 16 * 12, rel=1e-3)


def test_parse_holes_keeps_what_can_be_placed():
    reply = json.dumps({"holes": [
        {"label": "bolt holes", "diameter": 7, "axis": "z", "length": 40.2, "through": True,
         "centres": [[40, 122, 20.1], [40, 23.9, 20.1]]},
        {"label": "inclined", "diameter": 4.5, "axis": "other", "length": 10, "through": False, "centres": [[1, 2, 3]]},
        {"label": "bad", "diameter": 0, "axis": "x", "length": 5, "through": False, "centres": [[1, 2, 3]]}]})
    holes = flow.parse_holes("noise " + reply)
    assert [(h["diameter"], h["axis"], h["through"]) for h in holes] == [(7.0, "z", True)] * 2
    assert flow.parse_holes("not json") == []


def test_flow_runs_from_the_brief_alone(case_dir, tmp_path, monkeypatch):
    """No facts.json, model stubbed: the graph reads the brief, builds the hull and cuts the holes the model lists."""
    reply = json.dumps({"holes": [
        {"label": "blind bore", "diameter": 8, "axis": "x", "length": 8, "through": False, "centres": [[4, 15, 28]]},
        {"label": "through hole", "diameter": 10, "axis": "z", "length": 10, "through": True, "centres": [[40, 15, 5]]}]})
    monkeypatch.setattr(flow.ChatClient, "chat", lambda self, messages, timeout, temperature=None, extra=None:
                        (reply, {"prompt_tokens": 10, "completion_tokens": 5}))
    monkeypatch.setenv("AGENT_ENGINE", "flow")
    monkeypatch.setenv("AGENT_MAX_STEPS", "1")
    monkeypatch.setenv("AGENT_CANDIDATES", "1")
    monkeypatch.setenv("AGENT_CARVE_MODE", "features")
    settings = Settings.from_env()
    out = tmp_path / "out" / "test_case_1.stp"
    views = sorted((case_dir / "images").glob("view_*.png"))
    summary = flow.run_case(settings, "test_case_1", case_dir, (case_dir / "description.md").read_text(), views, None,
                            out, time.time() + 300, Tracer().case("test_case_1"))
    assert summary["status"] == "ok" and out.is_file()
    assert summary["through_holes"] == 1
    assert summary["iou_mean"] > 0.97                      # the silhouette comparison works without facts.json
    assert abs(summary["volume_err"]) < 0.02               # the blind bore was cut: the hull alone is heavier
