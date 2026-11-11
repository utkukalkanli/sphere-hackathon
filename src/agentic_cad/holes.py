"""Step 2, deterministic: cut the cylindrical holes that facts.json lists.

The silhouettes only find holes that go all the way through along a view direction. facts.json lists
every full cylindrical hole or bore of the part, with its diameter, axis, length and centre, including
blind holes and holes hidden behind other geometry. Each one is cut as a cylinder.

A bore can have a boss standing inside it (an annular groove): a listed boss that is coaxial with a
hole and thinner than it is kept, by leaving it out of the cylinder that is cut.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad.visual_hull import largest_solid, volume

AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


@dataclass
class Cylinder:
    centre: tuple[float, float, float]   # midpoint of the axis, mm from the bounding-box min corner
    axis: tuple[float, float, float]     # unit vector
    radius: float
    length: float
    group: int = 0                       # index of the facts.json entry it came from
    keep: tuple["Cylinder", ...] = ()    # bosses standing inside this hole: not cut


def _axis(label) -> tuple[float, float, float] | None:
    if label in AXES:
        return AXES[label]
    numbers = re.findall(r"-?\d+(?:\.\d+)?", str(label))
    if len(numbers) != 3:
        return None
    vector = np.array(numbers, dtype=float)
    norm = float(np.linalg.norm(vector))
    return tuple(vector / norm) if norm > 1e-6 else None


def _cylinders(entries) -> list[Cylinder]:
    result = []
    for group, entry in enumerate(entries or []):
        axis = _axis(entry.get("axis"))
        diameter, lengths = entry.get("diameter") or 0, entry.get("lengths") or []
        if axis is None or diameter <= 0 or not lengths:
            continue
        # `lengths` holds the distinct lengths of the group, not one per centre. With more than one,
        # which hole has which is unknown: take the shortest, so nothing is cut deeper than it really is.
        length = float(min(lengths))
        for centre in entry.get("centres") or []:
            if len(centre) == 3 and length > 0:
                result.append(Cylinder(tuple(map(float, centre)), axis, float(diameter) / 2, length, group))
    return result


def read_holes(case_dir: Path | None) -> list[Cylinder]:
    """The holes of facts.json, each with the coaxial bosses that stand inside it."""
    try:
        facts = json.loads((case_dir / "facts.json").read_text())
    except Exception:
        return []
    holes = _cylinders(facts.get("cylindrical_holes_and_bores"))
    bosses = _cylinders(facts.get("cylindrical_shafts_and_bosses"))
    for hole in holes:
        axis, centre = np.array(hole.axis), np.array(hole.centre)
        inside = []
        for boss in bosses:
            if boss.radius >= hole.radius or abs(float(np.dot(axis, boss.axis))) < 0.999:
                continue
            offset = np.array(boss.centre) - centre
            along = float(np.dot(offset, axis))
            off_axis = float(np.linalg.norm(offset - along * axis))
            if off_axis <= 0.02 + 0.01 * hole.radius and abs(along) < (hole.length + boss.length) / 2:
                inside.append(boss)
        hole.keep = tuple(inside)
    return holes


def _solid(cylinder: Cylinder) -> TopoDS_Shape:
    start = np.array(cylinder.centre) - np.array(cylinder.axis) * cylinder.length / 2
    frame = gp_Ax2(gp_Pnt(*map(float, start)), gp_Dir(*map(float, cylinder.axis)))
    return BRepPrimAPI_MakeCylinder(frame, cylinder.radius, cylinder.length).Shape()


def cut_holes(shape: TopoDS_Shape, holes: list[Cylinder]) -> TopoDS_Shape | None:
    """Remove all `holes` from `shape` in one boolean operation. One valid solid, or None."""
    if not holes:
        return shape
    tools = List_TopoDS_Shape()
    for hole in holes:
        tool = _solid(hole)
        for boss in hole.keep:
            tool = BRepAlgoAPI_Cut(tool, _solid(boss)).Shape()
        tools.Append(tool)
    arguments = List_TopoDS_Shape()
    arguments.Append(shape)
    operation = BRepAlgoAPI_Cut()
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.Build()
    if not operation.IsDone():
        return None
    solid = largest_solid(operation.Shape())
    if solid is None or volume(solid) <= 0 or not BRepCheck_Analyzer(solid).IsValid():
        return None
    return solid
