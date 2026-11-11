"""Cuts given as numbers: a solid to remove, in the part's frame (mm, bounding-box min corner at the
origin). Three kinds:

- cylinder: centre, axis, diameter, length (it runs length/2 to each side of the centre);
- box: min and max corner;
- prism: a planar polygon given as 3D points, swept `depth` mm along `direction`. Slanted edges are
  just points of the polygon.

The holes of facts.json are cut by `holes` (step 2), not here. These cuts come from a model reading
the brief (`llm_cuts`, step 3); none of them is trusted, `hull_agent` checks each one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad import visual_hull as vh

EXTEND_MM = 0.05  # cylinders run this much past both ends, so their caps never coincide with a face
AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


@dataclass
class Cut:
    name: str
    kind: str                      # cylinder, box, prism
    params: dict
    source: str = "llm"
    group: str = ""                # cuts of one group are tried together first
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict:
        return {"name": self.name, "kind": self.kind, "source": self.source, **self.params}


def _vector(value) -> np.ndarray:
    v = np.asarray(value, float).reshape(3)
    norm = np.linalg.norm(v)
    if not np.isfinite(norm) or norm < 1e-9:
        raise ValueError(f"not a direction: {value!r}")
    return v / norm


def parse_axis(axis) -> np.ndarray:
    """'X' / 'Y' / 'Z', '(-0.00, 0.71, 0.71)' or a list of three numbers."""
    if isinstance(axis, str):
        if axis.strip().upper() in AXES:
            return np.array(AXES[axis.strip().upper()])
        numbers = re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", axis)
        if len(numbers) == 3:
            return _vector([float(n) for n in numbers])
        raise ValueError(f"unknown axis {axis!r}")
    return _vector(axis)


def tool(cut: Cut) -> TopoDS_Shape:
    """The solid that `cut` removes. Raises ValueError for a cut that makes no proper solid."""
    p = cut.params
    if cut.kind == "cylinder":
        axis, centre = parse_axis(p["axis"]), np.asarray(p["centre"], float).reshape(3)
        length, radius = float(p["length"]) + 2 * EXTEND_MM, float(p["diameter"]) / 2
        if radius <= 0 or length <= 0:
            raise ValueError("cylinder needs a positive diameter and length")
        base = centre - axis * length / 2
        return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*map(float, base)), gp_Dir(*map(float, axis))),
                                        radius, length).Shape()
    if cut.kind == "box":
        low = np.minimum(np.asarray(p["min"], float), np.asarray(p["max"], float)).reshape(3)
        high = np.maximum(np.asarray(p["min"], float), np.asarray(p["max"], float)).reshape(3)
        if np.any(high - low <= 0):
            raise ValueError("box needs a positive size in X, Y and Z")
        return BRepPrimAPI_MakeBox(gp_Pnt(*map(float, low)), gp_Pnt(*map(float, high))).Shape()
    if cut.kind == "prism":
        points = np.asarray(p["points"], float)
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
            raise ValueError("prism needs at least 3 points [x, y, z]")
        normal = np.zeros(3)
        for i in range(len(points)):  # any three non-collinear points give the plane
            normal = np.cross(points[(i + 1) % len(points)] - points[i], points[(i + 2) % len(points)] - points[i])
            if np.linalg.norm(normal) > 1e-6:
                break
        normal = _vector(normal)
        off_plane = np.abs((points - points[0]) @ normal).max()
        if off_plane > 0.01 * max(1.0, np.ptp(points, axis=0).max()):
            raise ValueError(f"prism points are not in one plane (off by {off_plane:.2f} mm)")
        depth = float(p["depth"])
        if depth <= 0:
            raise ValueError("prism needs a positive depth")
        polygon = BRepBuilderAPI_MakePolygon()
        for point in points:
            polygon.Add(gp_Pnt(*map(float, point)))
        polygon.Close()
        face = BRepBuilderAPI_MakeFace(polygon.Wire(), True)
        if not face.IsDone() or not BRepCheck_Analyzer(face.Face()).IsValid():
            raise ValueError("prism outline does not make a face (self-intersecting?)")
        direction = _vector(p["direction"]) * depth
        return BRepPrimAPI_MakePrism(face.Face(), gp_Vec(*map(float, direction))).Shape()
    raise ValueError(f"unknown cut kind {cut.kind!r}")


def subtract(shape: TopoDS_Shape, cuts: list[Cut]) -> TopoDS_Shape | None:
    """Remove all `cuts` from `shape` in one boolean operation. One valid solid, or None. Module-level
    and with picklable arguments, so `vh.Cutter.run` can run it in a child process."""
    if not cuts:
        return shape
    tools = List_TopoDS_Shape()
    for cut in cuts:
        tools.Append(tool(cut))
    arguments = List_TopoDS_Shape()
    arguments.Append(shape)
    operation = BRepAlgoAPI_Cut()
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.Build()
    if not operation.IsDone():
        return None
    solid = vh.largest_solid(operation.Shape())
    if solid is None or vh.volume(solid) <= 0 or not BRepCheck_Analyzer(solid).IsValid():
        return None
    return solid
