"""Checks on the file we hand in, not on the solid in memory."""

from __future__ import annotations

import tempfile
from pathlib import Path

from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import visual_hull as vh


def reads_back_valid(shape: TopoDS_Shape) -> bool:
    """Whether the shape, written as a .stp and read again, is still one valid solid.

    Some faces pass every check in memory and come back broken from the file (practice part 4: a
    cone almost flat enough to be a plane, left by revolving a line 1.3 degrees off square to the
    axis). The file is what gets scored, so a step that makes curved faces should ask this."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.stp"
            vh.write_step(shape, path)
            reader = STEPControl_Reader()
            if reader.ReadFile(str(path)) != IFSelect_RetDone:
                return False
            reader.TransferRoots()
            read = reader.OneShape()
        return (read is not None and not read.IsNull() and read.ShapeType() == TopAbs_SOLID
                and BRepCheck_Analyzer(read).IsValid())
    except Exception:
        return False
