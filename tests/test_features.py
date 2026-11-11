from pathlib import Path

import numpy as np
import pytest

from agentic_cad import features, llm_cuts
from agentic_cad import visual_hull as vh
from agentic_cad.views import IsoCamera, OrthoCamera

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_parse_axis():
    assert features.parse_axis("y").tolist() == [0, 1, 0]
    assert np.allclose(features.parse_axis("(-0.00, 0.71, 0.71)"), [0, 2 ** -0.5, 2 ** -0.5], atol=1e-3)
    assert np.allclose(features.parse_axis([0, 0, 2]), [0, 0, 1])
    with pytest.raises(ValueError):
        features.parse_axis([0, 0, 0])


def test_cylinder_cut_removes_its_volume():
    block = vh.box({"x": 20, "y": 10, "z": 20})
    hole = features.Cut("h", "cylinder", {"centre": [10, 5, 10], "axis": "Y", "diameter": 4, "length": 10})
    result = vh.subtract(block, features.tool(hole))
    assert vh.volume(result) == pytest.approx(4000 - np.pi * 4 * 10, rel=1e-3)


def test_slanted_prism_from_points():
    # a 3 mm, 45 degree chamfer along the top front edge of a 10 mm cube, given as its triangle's corners
    block = vh.box({"x": 10, "y": 10, "z": 10})
    chamfer = features.Cut("c", "prism", {"points": [[-1, -1, 6], [-1, 4, 11], [-1, -1, 11]],
                                          "direction": [1, 0, 0], "depth": 12})
    result = vh.subtract(block, features.tool(chamfer))
    assert vh.volume(result) == pytest.approx(1000 - 0.5 * 3 * 3 * 10, rel=1e-3)


def test_prism_rejects_points_off_a_plane():
    cut = features.Cut("p", "prism", {"points": [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 5]],
                                      "direction": [0, 0, 1], "depth": 1})
    with pytest.raises(ValueError):
        features.tool(cut)


def test_holes_from_facts_include_angled_bores():
    cuts = features.holes_from_facts(REPO_ROOT / "artifacts/inputs/test_case_9")
    angled = [c for c in cuts if c.params["diameter"] == 127.0 and abs(c.params["axis"][2]) > 0.5]
    assert len(angled) == 6
    assert all(features.tool(c) is not None for c in cuts)


def test_llm_answer_parsing_drops_bad_entries():
    answer = """<think>plan</think>```json
    {"cuts": [{"name": "pocket", "kind": "box", "min": [0, 0, 0], "max": [1, 1, 1]},
              {"name": "nonsense", "kind": "sphere"}, "x"]}
    ```"""
    cuts = llm_cuts.parse(answer)
    assert [c.name for c in cuts] == ["pocket"] and cuts[0].source == "llm"


def test_cameras_put_the_box_where_the_views_show_it():
    dims = np.array([10.0, 20.0, 30.0])
    front = OrthoCamera("front", dims, (301, 101))  # X right, Z up
    assert np.allclose(front(np.array([[0, 0, 0], [10, 5, 30]])), [[0, 300], [100, 0]])
    back = OrthoCamera("back", dims, (301, 101))  # X left
    assert np.allclose(back(np.array([[0, 0, 0]])), [[100, 300]])
    iso = IsoCamera(vh.ISO_RIGHT, dims, 1000, 0.1)
    centre = iso(dims[None] / 2)[0]
    assert np.allclose(centre, [499.5, 499.5])
    # from (+X, -Y, +Z) the +X end of the box is to the right and the +Z end is higher up
    assert iso(np.array([[10, 10, 15]]))[0][0] > centre[0]
    assert iso(np.array([[5, 10, 30]]))[0][1] < centre[1]
