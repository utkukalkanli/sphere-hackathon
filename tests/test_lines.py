from pathlib import Path

import numpy as np

from agentic_cad import edges, line_cuts
from agentic_cad import visual_hull as vh
from agentic_cad.views import ViewSet

CASE = Path(__file__).resolve().parents[1] / "artifacts/inputs/test_case_1"


def test_render_lines_find_the_face_boundaries():
    lines = edges.render_lines(CASE / "images/view_4_top.png", crop=True)
    # the top view of practice 1 has one line across, at the rib (about a quarter down), and the outline
    rows = np.where(lines[:, lines.shape[1] // 2])[0]
    assert any(0.2 < r / lines.shape[0] < 0.3 for r in rows)


def test_line_drawing_of_the_box_matches_its_outline():
    views = sorted((CASE / "images").glob("view_*.png"))
    dims = vh.read_dimensions(CASE)
    box = vh.box(dict(zip("xyz", dims)))
    line_views = edges.LineViews(views, np.array(dims), edges.cameras_for(ViewSet(views, dims, box)))
    drawn = line_views.drawn(box)
    assert set(drawn) == {"front", "back", "left", "right", "top", "bottom",
                          "iso_front_right_top", "iso_back_left_bottom"}
    front = drawn["front"]
    assert front[0].mean() > 0.9 and front[-1].mean() > 0.9  # top and bottom edge of the front face


def test_depths_come_from_the_side_views():
    views = sorted((CASE / "images").glob("view_*.png"))
    dims = vh.read_dimensions(CASE)
    line_views = edges.LineViews(views, np.array(dims), edges.cameras_for(ViewSet(views, dims, vh.box(dict(zip("xyz", dims))))))
    # seen from the back, the rib stands 5 mm proud of the plate's back face
    assert any(abs(d - 5.0) < 0.2 for d in line_cuts.depth_candidates("back", line_views, np.array(dims)))
