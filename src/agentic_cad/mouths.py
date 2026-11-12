"""Part of step 2: a listed hole whose mouth a view draws must be open to that view.

facts.json gives every hole's two ends exactly. An end of a hole is either its bottom or a mouth on
a face of the part. Where an end lies buried in our solid, and a view that looks at it from outside
draws a line all around the circle of that end, and our solid has no edge there that would explain
the line, the view is showing the mouth: the part has nothing between that mouth and the camera.
The mouth is swept towards the camera and cut out. (Practice 9: the cylinder bores open into a
crankcase that no silhouette shows; the front view draws their inner mouths 87 mm under our surface.)

Judged on its own evidence, not on the part's volume: the cut is right where the line is.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad import holes as hl
from agentic_cad import spans, trace, verify
from agentic_cad import visual_hull as vh

ENABLED = os.environ.get("HULL_MOUTHS", "1") != "0"
TIMEOUT = float(os.environ.get("HULL_MOUTHS_TIMEOUT", 40))
ON_LINES = 0.75      # share of the end's circle that must lie on lines of the view
EXPLAINED = 0.5      # ... and at most this share on edges our own solid draws there
FACING = 0.5         # the view looks at the end at least this squarely (cosine)
MIN_PX = 8           # a circle smaller than this in the view is not judged
BURIED_PX = 2.0      # material beyond the end, in pixels, before it counts as buried
VIEWS = verify.ORTHO_VIEWS + ("iso_front_right_top", "iso_back_left_bottom")


def _cover(point: np.ndarray, direction: np.ndarray, intersector) -> float | None:
    """How much of the solid lies on the ray from `point` along `direction`."""
    stretches = spans._material(point, direction, intersector)
    if stretches is None:
        return None
    return float(sum(b - max(a, 0.0) for a, b in stretches if b > 1e-3))


def find(solid: TopoDS_Shape, holes: list[hl.Cylinder], observed: dict, dims: dict[str, float]) -> list[dict]:
    """The buried ends of `holes` that a view shows as a mouth: where, which way the hole leaves the
    part there, and towards which camera the mouth is open."""
    pixel = max(dims.values()) * vh.FRAME_MARGIN / verify.SIZE
    intersector = spans._intersector(solid)
    drawings, mesh, found = {}, None, []
    for hole in holes:
        axis, centre = np.array(hole.axis, float), np.array(hole.centre)
        for sign in (1, -1):
            outward = sign * axis
            end = centre + outward * hole.length / 2
            beyond = _cover(end, outward, intersector)
            if beyond is None or beyond < BURIED_PX * pixel:
                continue  # open already, or the bottom of a hole we cannot tell from a mouth
            best = None
            for name in VIEWS:
                view = observed.get(name)
                towards = verify.camera(name)[2]
                if view is None or float(towards @ outward) < FACING or 2 * hole.radius / view.mm_per_px < MIN_PX:
                    continue
                rim = spans._pixels(spans._circle(end, outward, hole.radius, 120), view, dims).round().astype(int)
                if rim.min() < 2 or rim.max() > view.image.shape[0] - 3:
                    continue
                lines = cv2.dilate(view.lines.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
                share = float(lines[rim[:, 1], rim[:, 0]].mean())
                if share < ON_LINES or (best is not None and share <= best[0]):
                    continue
                if name not in drawings:  # our own edges there: a line they explain proves nothing
                    mesh = mesh or verify.tessellate(solid, 0.0005 * max(dims.values()), 0.2)
                    drawing = verify.draw(mesh, name, dims, view.mm_per_px)
                    drawings[name] = cv2.dilate(drawing.sharp.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
                if float(drawings[name][rim[:, 1], rim[:, 0]].mean()) > EXPLAINED:
                    continue
                covered = [_cover(point, towards, intersector)
                           for point in [end, *spans._circle(end, outward, 0.7 * hole.radius, 4)]]
                if any(c is None for c in covered) or max(covered) < BURIED_PX * pixel:
                    continue
                best = (share, name, towards, float(np.median(covered)))
            if best is not None:
                found.append({"end": tuple(map(float, end)), "outward": tuple(map(float, outward)),
                              "radius": hole.radius, "view": best[1], "towards": tuple(map(float, best[2])),
                              "on_lines": round(best[0], 2), "covered_mm": round(best[3], 2),
                              "reach": 4 * float(np.linalg.norm(list(dims.values())))})
    return found


def cut(shape: TopoDS_Shape, mouths: list[dict]) -> TopoDS_Shape | None:
    """Remove from `shape` what lies between each mouth and its camera. One valid solid, or None."""
    tools = List_TopoDS_Shape()
    for mouth in mouths:
        frame = gp_Ax2(gp_Pnt(*mouth["end"]), gp_Dir(*mouth["outward"]))
        edge = BRepBuilderAPI_MakeEdge(gp_Circ(frame, mouth["radius"])).Edge()
        disc = BRepBuilderAPI_MakeFace(BRepBuilderAPI_MakeWire(edge).Wire()).Face()
        towards = np.array(mouth["towards"]) * mouth["reach"]
        tools.Append(BRepPrimAPI_MakePrism(disc, gp_Vec(*map(float, towards))).Shape())
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


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None) -> TopoDS_Shape:
    """Runs right after the holes are cut."""
    if not ENABLED or case_dir is None:
        return solid
    with trace.step("open_mouths", kind="code",
                    description="Opens listed holes whose mouth a view draws under our surface") as step:
        holes = hl.read_holes(case_dir)
        if not holes:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        observed = verify.observe(case_dir, dims)
        mouths = find(solid, holes, observed, dims)
        step.metric(mouths=len(mouths))
        if not mouths:
            step.log("no listed hole ends buried under a mouth that a view draws")
            return solid
        step.attach("mouths.json", mouths)
        target = vh.read_volume(case_dir)
        carved = agent._guarded(step, solid, target, lambda: agent.cutter.run(cut, solid, mouths, TIMEOUT))
        if carved is None:
            step.log("cutting all mouths at once was refused; trying them one at a time")
            carved, kept = solid, 0
            for mouth in mouths:
                attempt = agent._guarded(step, carved, target, lambda: agent.cutter.run(cut, carved, [mouth], TIMEOUT / 2),
                                         quiet=True)
                if attempt is not None:
                    carved, kept = attempt, kept + 1
            step.metric(mouths_cut=kept)
            if kept == 0:
                return solid
            step._status = None
        agent._record(step, carved, "mouths")
        return carved
