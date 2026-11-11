"""Step 2: cut what the silhouettes cannot see (holes, bores, pockets, recesses) as exact geometry.

A cut is a solid to remove, given by numbers in the part's frame (mm, bounding-box min corner at the
origin): a cylinder (centre, axis, diameter, length), a box (min, max corner), or a prism (a planar
polygon given as 3D points, extruded along a direction). Slanted edges are just points of a polygon.

Cuts come from `facts.json` (every listed hole and bore, angled ones included) and, optionally, from
a model reading the brief (`llm_cuts`). None of them is trusted: a cut is kept only if
  - the solid stays at or above the part's true volume (with a margin), and
  - no view starts showing background where the render shows the part.
Interior cuts are invisible in the silhouettes by nature, so they can only be checked against these
two constraints; a blind hole cut too deep through a wall is caught by the second.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.TopoDS import TopoDS_Builder, TopoDS_Compound, TopoDS_Shape

EXTEND_MM = 0.05  # cylinders run this much past both ends, so their caps never coincide with a face
AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


@dataclass
class Cut:
    name: str
    kind: str                      # cylinder, box, prism
    params: dict
    source: str = "facts"          # facts, llm
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
    """The solid that `cut` removes."""
    p = cut.params
    if cut.kind == "cylinder":
        axis, centre = parse_axis(p["axis"]), np.asarray(p["centre"], float)
        length, radius = float(p["length"]) + 2 * EXTEND_MM, float(p["diameter"]) / 2
        if radius <= 0 or length <= 0:
            raise ValueError("cylinder needs a positive diameter and length")
        base = centre - axis * length / 2
        return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*base), gp_Dir(*axis)), radius, length).Shape()
    if cut.kind == "box":
        low, high = np.minimum(p["min"], p["max"]), np.maximum(p["min"], p["max"])
        if np.any(high - low <= 0):
            raise ValueError("box needs a positive size in X, Y and Z")
        return BRepPrimAPI_MakeBox(gp_Pnt(*map(float, low)), gp_Pnt(*map(float, high))).Shape()
    if cut.kind == "prism":
        points = np.asarray(p["points"], float)
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
            raise ValueError("prism needs at least 3 points [x, y, z]")
        normal = np.cross(points[1] - points[0], points[2] - points[0])
        for i in range(len(points)):  # any three non-collinear points give the plane
            normal = np.cross(points[(i + 1) % len(points)] - points[i], points[(i + 2) % len(points)] - points[i])
            if np.linalg.norm(normal) > 1e-6:
                break
        normal = _vector(normal)
        off_plane = np.abs((points - points[0]) @ normal).max()
        if off_plane > 0.01 * max(1.0, np.ptp(points, axis=0).max()):
            raise ValueError(f"prism points are not in one plane (off by {off_plane:.2f} mm)")
        polygon = BRepBuilderAPI_MakePolygon()
        for point in points:
            polygon.Add(gp_Pnt(*map(float, point)))
        polygon.Close()
        face = BRepBuilderAPI_MakeFace(polygon.Wire(), True)
        if not face.IsDone():
            raise ValueError("prism outline does not make a face (self-intersecting?)")
        direction = _vector(p["direction"]) * float(p["depth"])
        return BRepPrimAPI_MakePrism(face.Face(), gp_Vec(*map(float, direction))).Shape()
    raise ValueError(f"unknown cut kind {cut.kind!r}")


def compound(shapes: list[TopoDS_Shape]) -> TopoDS_Shape:
    if len(shapes) == 1:
        return shapes[0]
    builder, result = TopoDS_Builder(), TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def holes_from_facts(case_dir: Path | None) -> list[Cut]:
    """Every hole and bore that facts.json lists with its centres, as a cylinder to cut.

    `centres` are the middles of the cylindrical faces and `lengths` their lengths; when an entry
    has several lengths it does not say which centre has which, so the shortest is used (a hole cut
    too short leaves material, one cut too long can break through a wall).
    """
    try:
        facts = json.loads((case_dir / "facts.json").read_text())
    except Exception:
        return []
    cuts = []
    for index, entry in enumerate(facts.get("cylindrical_holes_and_bores") or []):
        try:
            axis = parse_axis(entry["axis"])
            diameter = float(entry["diameter"])
            length = min(float(v) for v in entry["lengths"])
            centres = [np.asarray(c, float) for c in entry.get("centres") or []]
        except Exception:
            continue
        group = f"facts_{index}_d{diameter:g}"
        for n, centre in enumerate(centres):
            cuts.append(Cut(name=f"hole d{diameter:g} #{n + 1}", kind="cylinder", group=group,
                            params={"centre": centre.round(4).tolist(), "axis": axis.round(4).tolist(),
                                    "diameter": diameter, "length": length}))
    return cuts
