"""Checks on the file we hand in, not on the solid in memory."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import visual_hull as vh


def file_valid(path: Path) -> bool:
    """Whether the .stp at `path` reads as one valid solid."""
    try:
        reader = STEPControl_Reader()
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            return False
        reader.TransferRoots()
        read = reader.OneShape()
        return (read is not None and not read.IsNull() and read.ShapeType() == TopAbs_SOLID
                and BRepCheck_Analyzer(read).IsValid())
    except Exception:
        return False


def reads_back_valid(shape: TopoDS_Shape) -> bool:
    """Whether the shape, written as a .stp and read again, is still one valid solid.

    Some faces pass every check in memory and come back broken from the file (practice part 4: a
    cone almost flat enough to be a plane, left by revolving a line 1.3 degrees off square to the
    axis). The file is what gets scored, so a step that makes curved faces should ask this."""
    return checkpoint(shape, None, keep_faces=True)


def part_path(output_path: Path) -> Path:
    """Where a file waits, beside its output, for the moment it replaces it."""
    return output_path.with_name(f".{output_path.name}.part")


def clean(output_dir: Path) -> None:
    """Remove what a run that was killed between copying and renaming left behind: the output
    directory may hold nothing but test_case_<n>.stp."""
    try:
        for leftover in Path(output_dir).glob(".*.stp.part"):
            leftover.unlink(missing_ok=True)
    except Exception:
        pass


def publish(source: Path, output_path: Path) -> None:
    """Put a finished file in place of `output_path` in one step, so that a run killed at any moment
    leaves either the file that was there or the new one, never half of one. The copy has to wait
    in the output directory itself: a rename is only atomic within one file system."""
    part = part_path(output_path)
    try:
        shutil.copyfile(source, part)
        os.replace(part, output_path)
    finally:
        part.unlink(missing_ok=True)


def checkpoint(solid: TopoDS_Shape, output_path: Path | None, keep_faces: bool = False) -> bool:
    """Write `solid` as the answer at `output_path`, but only if the file reads back as one valid
    solid; coplanar faces are merged first unless `keep_faces`. Returns whether it did: on False the
    file that was there is untouched. Without an `output_path` it only says whether the file would
    have been valid."""
    try:
        # tidy works on a copy: ShapeUpgrade_UnifySameDomain changes the shape it is given (practice 8:
        # the solid the next step went on with had another volume, and that step cut nothing)
        shape = solid if keep_faces else vh.tidy(BRepBuilderAPI_Copy(solid, True, False).Shape())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.stp"
            vh.write_step(shape, path)
            if not file_valid(path):
                return False
            if output_path is not None:
                publish(path, Path(output_path))
        return True
    except Exception:
        return False
