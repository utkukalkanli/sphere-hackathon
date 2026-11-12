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
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeRevol
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt
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
    tip: float = 0.0                     # height of a drill point beyond the end the axis points to


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
    if cylinder.tip > 0:
        return _drilled(frame, cylinder.radius, cylinder.length, cylinder.tip)
    return BRepPrimAPI_MakeCylinder(frame, cylinder.radius, cylinder.length).Shape()


def _drilled(frame: gp_Ax2, radius: float, length: float, tip: float) -> TopoDS_Shape:
    """A cylinder that ends in a cone, as a drill leaves it: one solid, the half section revolved."""
    origin, along, across = frame.Location(), frame.Direction(), frame.XDirection()

    def point(r: float, z: float) -> gp_Pnt:
        return gp_Pnt(*(origin.Coord(k) + along.Coord(k) * z + across.Coord(k) * r for k in (1, 2, 3)))

    corners = [point(0, 0), point(radius, 0), point(radius, length), point(0, length + tip)]
    wire = BRepBuilderAPI_MakeWire()
    for first, second in zip(corners, corners[1:] + corners[:1]):
        wire.Add(BRepBuilderAPI_MakeEdge(first, second).Edge())
    face = BRepBuilderAPI_MakeFace(wire.Wire()).Face()
    return BRepPrimAPI_MakeRevol(face, gp_Ax1(origin, along)).Shape()


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


def outline_hints(case_dir: Path | None, u: str, v: str, along: str) -> dict:
    """What facts.json says about circles in the silhouette seen along box axis `along`, whose
    horizontal and vertical directions are the box axes `u` and `v`: every listed radius, and the
    projected centre and radius of each full cylinder whose axis points along the view."""
    try:
        facts = json.loads((case_dir / "facts.json").read_text())
    except Exception:
        return {}
    index = {"x": 0, "y": 1, "z": 2}
    radii = set()
    for entry in (facts.get("cylindrical_holes_and_bores") or []) + (facts.get("cylindrical_shafts_and_bosses") or []) \
            + (facts.get("partial_cylinders_arcs") or []):
        if entry.get("diameter"):
            radii.add(float(entry["diameter"]) / 2)
    for entry in facts.get("fillets_rounds_from_cylinders") or []:
        if entry.get("radius"):
            radii.add(float(entry["radius"]))
    circles = []
    for cylinder in _cylinders(facts.get("cylindrical_holes_and_bores")) + _cylinders(facts.get("cylindrical_shafts_and_bosses")):
        if abs(cylinder.axis[index[along]]) > 0.999:
            circles.append(((cylinder.centre[index[u]], cylinder.centre[index[v]]), cylinder.radius))
    return {"radii": sorted(radii), "circles": circles}
