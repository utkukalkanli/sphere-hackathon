import numpy as np
import pytest

from agentic_cad import features, llm_cuts
from agentic_cad import visual_hull as vh


def test_parse_axis():
    assert features.parse_axis("y").tolist() == [0, 1, 0]
    assert np.allclose(features.parse_axis("(-0.00, 0.71, 0.71)"), [0, 2 ** -0.5, 2 ** -0.5], atol=1e-3)
    assert np.allclose(features.parse_axis([0, 0, 2]), [0, 0, 1])
    with pytest.raises(ValueError):
        features.parse_axis([0, 0, 0])


def test_cylinder_and_box_cuts_remove_their_volume():
    block = vh.box({"x": 20, "y": 10, "z": 20})
    hole = features.Cut("h", "cylinder", {"centre": [10, 5, 10], "axis": "Y", "diameter": 4, "length": 10})
    pocket = features.Cut("p", "box", {"min": [1, 1, 15], "max": [5, 9, 21]})
    result = features.subtract(block, [hole, pocket])
    assert vh.volume(result) == pytest.approx(4000 - np.pi * 4 * 10 - 4 * 8 * 5, rel=1e-3)


def test_slanted_prism_from_points():
    # a 3 mm, 45 degree chamfer along the top front edge of a 10 mm cube, given as its triangle's corners
    block = vh.box({"x": 10, "y": 10, "z": 10})
    chamfer = features.Cut("c", "prism", {"points": [[-1, -1, 6], [-1, 4, 11], [-1, -1, 11]],
                                          "direction": [1, 0, 0], "depth": 12})
    result = features.subtract(block, [chamfer])
    assert vh.volume(result) == pytest.approx(1000 - 0.5 * 3 * 3 * 10, rel=1e-3)


def test_prism_rejects_points_off_a_plane():
    cut = features.Cut("p", "prism", {"points": [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 5]],
                                      "direction": [0, 0, 1], "depth": 1})
    with pytest.raises(ValueError):
        features.tool(cut)


def test_llm_answer_parsing_drops_bad_entries():
    answer = """<think>plan</think>```json
    {"cuts": [{"name": "pocket", "kind": "box", "min": [0, 0, 0], "max": [1, 1, 1]},
              {"name": "nonsense", "kind": "sphere"}, "x"]}
    ```"""
    cuts = llm_cuts.parse(answer)
    assert [c.name for c in cuts] == ["pocket"] and cuts[0].source == "llm"


def test_step_3_is_off_without_a_model(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert not llm_cuts.configured()


def test_llm_answer_with_arithmetic_in_numbers():
    answer = """{"cuts": [{"name": "a", "kind": "box", "min": [26.67 + 1.27, 66.04 - 66.04, -2],
              "max": [(10 + 2) / 2, 3*2, 1e1]}]}"""
    cuts = llm_cuts.parse(answer)
    assert cuts[0].params["min"] == [27.94, 0.0, -2] and cuts[0].params["max"] == [6.0, 6.0, 10.0]
