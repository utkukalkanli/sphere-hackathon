"""The evidence of a case (evidence.py): read once, shared, and a table a reader can use."""

from pathlib import Path

import cv2
import numpy as np

from agentic_cad import evidence, hull_agent, regions, verify
from agentic_cad import visual_hull as vh

CASE = Path(__file__).resolve().parents[1] / "artifacts/inputs/test_case_3"


def test_the_agent_reads_a_case_once():
    agent = hull_agent.VisualHullAgent()
    first = agent.evidence(CASE)
    assert agent.evidence(CASE) is first
    assert agent.evidence(CASE.with_name("test_case_1")) is not first


def test_the_areas_of_a_view_are_found_once():
    view = evidence.Evidence(CASE, dict(zip("xyz", vh.read_dimensions(CASE)))).observed["front"]
    assert regions.segment(view)[0] is regions.segment(view)[0]
    assert regions._measure(view)[1] is regions._measure(view)[1]


def test_the_table_has_a_row_per_numbered_region():
    dims = dict(zip("xyz", vh.read_dimensions(CASE)))
    table = evidence.Evidence(CASE, dims)
    box = vh.box(dims)
    rows = table.rows(box)
    assert table.rows(box) is rows
    found = table.regions()
    assert len(rows) == sum(len(items) for _, items in found.values())
    for view, (_, items) in found.items():
        assert [row["number"] for row in rows if row["view"] == view] == list(range(1, len(items) + 1))
    # under a box every region of a straight view lies on the box's face: one depth, no spread
    front = [row for row in rows if row["view"] == "front"]
    assert front and all(abs(row["depth_mm"] - dims["y"] / 2) < 1e-6 and row["depth_spread_mm"] == 0 for row in front)
    pictures = table.marked()
    assert set(pictures) == set(found)
    image = cv2.imdecode(np.frombuffer(pictures["front"], np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == table.observed["front"].image.shape
