"""Cuts as typed records: what a proposer (a model, see propose.py) may ask to be removed.

Deterministic. Millimetres, in the frame of the solid: the origin is the bounding-box min corner,
as in facts.json and the brief. A record is a dict:

    {"type": "box", "min": [x, y, z], "max": [x, y, z]}
    {"type": "cylinder", "p0": [x, y, z], "p1": [x, y, z], "radius": r}
    {"type": "prism", "axis": "X" | "Y" | "Z", "polygon": [[u, v], ...], "range": [a, b]}
        the polygon lies in the plane across the axis, (u, v) = (y, z) for X, (x, z) for Y, (x, y) for Z
    {"type": "halfspace", "point": [x, y, z], "normal": [x, y, z]}     removes the side the normal points to
    {"type": "region_floor", "view": "top", "region": 7, "depth": 12.5}
        the column over region 7 of that view (the numbers of evidence.rows and evidence.marked), cut
        down to 12.5 mm below the face of the bounding box that the view looks at
    {"type": "recess", "view": "top", "region": 7}
        the same column with the depth left open: whoever applies it looks for the depth (propose.py)

and may carry "id" and "confidence" (0..1). `normalise` checks a record and raises ValueError on a
malformed one; `tool_for` turns it into what `cut` subtracts; `cut(shape, tool)` runs in a child
process (`Cutter.run`). `deeper` gives the same record at another depth, where it has one.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import verify
from agentic_cad import visual_hull as vh

AXES = {"X": 0, "Y": 1, "Z": 2}
TYPES = ("box", "cylinder", "prism", "halfspace", "region_floor", "recess")
WORDS = {"very high": 0.95, "high": 0.9, "medium": 0.6, "low": 0.3, "very low": 0.1}
ALIASES = {"pocket": "recess", "cyl": "cylinder", "hole": "cylinder", "cuboid": "box", "extrude": "prism", "polygon": "prism",
           "plane": "halfspace", "halfplane": "halfspace", "regionfloor": "region_floor", "floor": "region_floor"}
MIN_SIZE = 1e-3      # mm: thinner than this is not a cut
GROW_PX = 2          # a region ends where its lines begin; the column reaches to the middle of them
REACH = 1e4          # mm: "to infinity" for a half space


def _number(value) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return number


def _vector(value, size: int = 3) -> np.ndarray:
    if isinstance(value, dict):
        value = [value[key] for key in "xyz"[:size]]
    numbers = [_number(v) for v in value]
    if len(numbers) != size:
        raise ValueError(f"expected {size} numbers, got {len(numbers)}")
    return np.array(numbers)


def confidence(record: dict) -> float:
    """0..1, whatever way it was written ("high", "80%", 80, 0.8); 0.5 when it cannot be read."""
    value = record.get("confidence", 0.5)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in WORDS:
            return WORDS[text]
        try:
            value = float(text.rstrip("%")) / (100.0 if text.endswith("%") else 1.0)
        except ValueError:
            return 0.5
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(value):
        return 0.5
    return min(1.0, max(0.0, value / 100.0 if value > 1.0 else value))


def normalise(record: dict) -> dict:
    """A clean copy with plain values. Raises ValueError (or KeyError, TypeError) on a malformed record."""
    kind = str(record.get("type", "")).strip().lower().replace("-", "_")
    kind = ALIASES.get(kind.replace("_", ""), kind)
    out = {"id": str(record.get("id", "?")), "type": kind, "confidence": confidence(record)}
    if kind == "box":
        a, b = _vector(record["min"]), _vector(record["max"])
        low, high = np.minimum(a, b), np.maximum(a, b)
        if np.any(high - low < MIN_SIZE):
            raise ValueError("box has no thickness")
        out.update(min=low.tolist(), max=high.tolist())
    elif kind == "cylinder":
        p0, p1 = _vector(record["p0"]), _vector(record["p1"])
        radius = _number(record["radius"]) if record.get("radius") is not None else _number(record["diameter"]) / 2
        if np.linalg.norm(p1 - p0) < MIN_SIZE or radius < MIN_SIZE:
            raise ValueError("cylinder has no length or radius")
        out.update(p0=p0.tolist(), p1=p1.tolist(), radius=radius)
    elif kind == "prism":
        axis = str(record["axis"]).strip().upper().lstrip("+-")
        if axis not in AXES:
            raise ValueError(f"axis {record['axis']!r}")
        polygon = [_vector(point, 2).tolist() for point in record["polygon"]]
        if len(polygon) > 3 and np.allclose(polygon[0], polygon[-1]):
            polygon = polygon[:-1]
        if len(polygon) < 3:
            raise ValueError("polygon with under 3 points")
        points = np.array(polygon)
        area = 0.5 * abs(np.sum(points[:, 0] * np.roll(points[:, 1], -1) - np.roll(points[:, 0], -1) * points[:, 1]))
        a, b = sorted(_vector(record["range"], 2).tolist())
        if area < MIN_SIZE or b - a < MIN_SIZE:
            raise ValueError("prism has no area or length")
        out.update(axis=axis, polygon=polygon, range=[a, b])
    elif kind == "halfspace":
        normal = _vector(record["normal"])
        if np.linalg.norm(normal) < 1e-9:
            raise ValueError("zero normal")
        out.update(point=_vector(record["point"]).tolist(), normal=(normal / np.linalg.norm(normal)).tolist())
    elif kind == "region_floor":
        view = str(record["view"]).strip().lower()
        if view not in verify.ORTHO_VIEWS:
            raise ValueError(f"view {record['view']!r}")
        depth = _number(record["depth"])
        if depth < MIN_SIZE:
            raise ValueError("no depth")
        out.update(view=view, region=int(record["region"]), depth=depth)
    elif kind == "recess":
        view = str(record["view"]).strip().lower()
        if view not in verify.ORTHO_VIEWS:
            raise ValueError(f"view {record['view']!r}")
        out.update(view=view, region=int(record["region"]))
    else:
        raise ValueError(f"unknown type {record.get('type')!r}")
    return out


def deeper(record: dict, factor: float) -> dict | None:
    """The normalised record with its depth multiplied by `factor`, or None for a kind without one."""
    if record["type"] != "region_floor":
        return None
    return {**record, "depth": record["depth"] * factor}


def _column(record: dict, evidence) -> vh.Silhouette:
    """The column over a region of a straight view, from `depth` below the box face towards the camera."""
    view = record["view"]
    seen, dims = evidence.observed[view], evidence.dims
    if "area" in record:   # a face of the view's drawing: it already reaches to the middle of its lines
        area = record["area"].astype(np.uint8) * 255
    else:
        found = evidence.regions().get(view)
        if found is None:
            raise ValueError(f"the {view} view could not be read")
        labels, items = found
        if not 1 <= record["region"] <= len(items):
            raise ValueError(f"the {view} view has no region {record['region']}")
        area = (labels == items[record["region"] - 1].label).astype(np.uint8)
        area = cv2.dilate(area, np.ones((2 * GROW_PX + 1, 2 * GROW_PX + 1), np.uint8)) * 255
    right, up, towards = verify.camera(view)
    size = np.array([dims["x"], dims["y"], dims["z"]])
    centre, half = size / 2, seen.mask.shape[0] / 2
    level = float(abs(towards) @ size) / 2 - record["depth"]   # along the view axis, from the centre of the box

    def to_mm(pixels: np.ndarray) -> np.ndarray:
        return np.column_stack([(pixels[:, 0] + 0.5 - half) * seen.mm_per_px, (half - 0.5 - pixels[:, 1]) * seen.mm_per_px])

    outlines = vh.trace_outlines(area, to_mm, seen.mm_per_px)
    if not outlines:
        raise ValueError("the region has no outline that can be cut")
    return vh.Silhouette(name=f"region_{view}_{record.get('region', record.get('id'))}", views=[view], mask=area, outlines=outlines,
                         origin=centre + level * towards, right=right, up=up, direction=towards,
                         depth=2 * float(np.linalg.norm(centre)), info={"level": round(level, 3)})


def tool_for(record: dict, evidence=None):
    """What `cut` subtracts for a normalised record: the record itself, or for a region_floor (which
    needs the views, `evidence`) the column as a silhouette. Picklable either way."""
    if record["type"] == "region_floor":
        if evidence is None:
            raise ValueError("a region_floor needs the evidence of the case")
        return _column(record, evidence)
    return record


def _solid(tool) -> TopoDS_Shape:
    if isinstance(tool, vh.Silhouette):
        return vh.prism(tool)
    kind = tool["type"]
    if kind == "box":
        return BRepPrimAPI_MakeBox(gp_Pnt(*tool["min"]), gp_Pnt(*tool["max"])).Shape()
    if kind == "cylinder":
        p0, p1 = np.array(tool["p0"]), np.array(tool["p1"])
        along = p1 - p0
        return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*p0), gp_Dir(*along)), tool["radius"], float(np.linalg.norm(along))).Shape()
    if kind == "prism":
        k = AXES[tool["axis"]]
        others = [i for i in range(3) if i != k]   # X: (y, z); Y: (x, z); Z: (x, y)
        a, b = tool["range"]
        wire = BRepBuilderAPI_MakePolygon()
        for u, v in tool["polygon"]:
            point = [0.0, 0.0, 0.0]
            point[k], point[others[0]], point[others[1]] = a, u, v
            wire.Add(gp_Pnt(*point))
        wire.Close()
        face = BRepBuilderAPI_MakeFace(wire.Wire(), True)
        if not face.IsDone():
            raise ValueError("the polygon is not a face")
        step = [0.0, 0.0, 0.0]
        step[k] = b - a
        return BRepPrimAPI_MakePrism(face.Face(), gp_Vec(*step)).Shape()
    if kind == "halfspace":   # a large box standing on the plane, on the side the normal points to
        normal = np.array(tool["normal"])
        helper = np.array([1.0, 0, 0]) if abs(normal[0]) < 0.9 else np.array([0, 1.0, 0])
        x = np.cross(helper, normal)
        x /= np.linalg.norm(x)
        corner = np.array(tool["point"]) - REACH * (x + np.cross(normal, x))
        return BRepPrimAPI_MakeBox(gp_Ax2(gp_Pnt(*corner), gp_Dir(*normal), gp_Dir(*x)), 2 * REACH, 2 * REACH, REACH).Shape()
    raise ValueError(kind)


def cut(shape: TopoDS_Shape, tool) -> TopoDS_Shape | None:
    """`shape` without the tool: the largest piece left, if it is one valid solid that holds nearly
    all of what is left (a cut that splits the part in two is not a recess). Runs in a child process."""
    done = BRepAlgoAPI_Cut(shape, _solid(tool))
    if not done.IsDone():
        return None
    solid = vh.largest_solid(done.Shape())
    if solid is None or vh.volume(solid) <= 0 or not BRepCheck_Analyzer(solid).IsValid():
        return None
    if vh.volume(solid) < 0.999 * vh.volume(done.Shape()):
        return None
    return solid
