"""The sources read off the views alone (sources.py), on a practice part whose faces are known."""

from pathlib import Path

import numpy as np

from agentic_cad import sources, tone, verify
from agentic_cad import visual_hull as vh

CASE = Path(__file__).resolve().parents[1] / "artifacts" / "inputs" / "test_case_1"


def test_axis_faces_of_a_box_shaped_part_get_their_planes():
    dims = dict(zip("xyz", vh.read_dimensions(CASE)))
    observed = verify.observe(CASE, dims)
    found = sources.faces(observed, dims, tone.tables()[tone.DEFAULT])
    sure = {(face.view, "xyz"[face.axis], face.sign): face.level for _, listed in found.values() for face in listed if face.sure}
    # the outer faces of the bracket lie on its bounding box
    assert abs(sure[("iso_front_right_top", "z", 1)] - dims["z"]) < 0.3
    assert abs(sure[("iso_back_left_bottom", "x", -1)]) < 0.3
    assert abs(sure[("iso_back_left_bottom", "z", -1)]) < 0.3

    normals = sources.normals(observed, found)
    depth = sources.depth(observed, dims, found)
    for view, (labels, listed) in found.items():
        assert normals[view].shape == (*labels.shape, 3)
        known = np.isfinite(depth[view])
        assert known.sum() == sum(face.area for face in listed if face.sure)
        # a point of a face lies inside the box, seen from the centre: within half its diagonal
        assert np.abs(depth[view][known]).max() <= np.linalg.norm(list(dims.values())) / 2 + 0.5


def test_tone_steps_are_not_lines():
    observed = verify.observe(CASE)
    for seen in observed.values():
        jumps = sources.steps(seen)
        assert not (jumps & seen.lines).any() and not (jumps & ~seen.mask).any()
