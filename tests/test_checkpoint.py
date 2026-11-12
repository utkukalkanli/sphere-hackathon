"""Every case always has a valid file: what export.checkpoint and VisualHullAgent.refine promise."""

from types import SimpleNamespace

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer

from agentic_cad import export
from agentic_cad import hull_agent
from agentic_cad import visual_hull as vh

BOX = {"x": 30.0, "y": 20.0, "z": 10.0}


def a_face(solid):
    """Not a solid: what a step that went wrong might hand back."""
    return TopExp_Explorer(solid, TopAbs_FACE).Current()


def halved(agent, solid, case_dir, views):
    return BRepAlgoAPI_Cut(solid, BRepPrimAPI_MakeBox(15.0, 20.0, 10.0).Shape()).Shape()


def raises(agent, solid, case_dir, views):
    raise RuntimeError("a step that fails")


def broken(agent, solid, case_dir, views):
    return a_face(solid)


def volume_of_file(path) -> float:
    from OCP.STEPControl import STEPControl_Reader
    reader = STEPControl_Reader()
    reader.ReadFile(str(path))
    reader.TransferRoots()
    return vh.volume(reader.OneShape())


def test_checkpoint_writes_a_valid_file_and_nothing_else(tmp_path):
    output = tmp_path / "test_case_1.stp"
    assert export.checkpoint(vh.box(BOX), output)
    assert export.file_valid(output)
    assert [p.name for p in tmp_path.iterdir()] == ["test_case_1.stp"]


def test_checkpoint_refuses_what_is_not_a_solid_and_keeps_the_file(tmp_path):
    output = tmp_path / "test_case_1.stp"
    export.checkpoint(vh.box(BOX), output)
    before = output.read_bytes()
    assert not export.checkpoint(a_face(vh.box(BOX)), output)
    assert output.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["test_case_1.stp"]


def test_clean_removes_what_a_killed_run_left(tmp_path):
    output = tmp_path / "test_case_1.stp"
    export.checkpoint(vh.box(BOX), output)
    export.part_path(output).write_text("half a file")
    export.clean(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["test_case_1.stp"]


@pytest.mark.parametrize("bad", [raises, broken])
def test_a_bad_step_costs_only_that_step(tmp_path, monkeypatch, bad):
    """The step before the bad one and the step after it both end up in the file."""
    steps = {"first": halved, "bad": bad, "last": halved}
    monkeypatch.setattr(hull_agent, "LATER_STEPS", {name: SimpleNamespace(apply=f) for name, f in steps.items()})
    monkeypatch.setattr(hull_agent, "SKIP_STEPS", set())
    output = tmp_path / "test_case_1.stp"
    agent = hull_agent.VisualHullAgent()
    solid = vh.box(BOX)
    assert agent.checkpoint(solid, output)
    solid = agent.refine(solid, None, [], output_path=output)
    # `halved` takes x under 15 away; the second time there is nothing left there to take
    assert vh.volume(solid) == pytest.approx(3000.0)
    assert export.file_valid(output)
    assert volume_of_file(output) == pytest.approx(3000.0)
    assert [p.name for p in tmp_path.iterdir()] == ["test_case_1.stp"]
