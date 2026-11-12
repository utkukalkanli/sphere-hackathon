"""Part of step 2: a listed hole whose mouth a view draws must be open to that view.

facts.json gives every hole's two ends exactly. An end of a hole is either its bottom or a mouth on
a face of the part. Where an end lies buried in our solid, and a view that looks at it from outside
draws a line all around the circle of that end, and our solid has no edge there that would explain
the line, the view is showing the mouth: the part has nothing between that mouth and the camera.
The mouth is swept towards the camera and cut out. (Practice 9: the cylinder bores open into a
crankcase that no silhouette shows; the front view draws their inner mouths 87 mm under our surface.)

The plane through such a mouth, square to the hole, is the face the hole was made from. The area
of the view right around the mouth, if it is one even tone and the tone such a face has there, is
that face as far as the view shows it, and it too is cut down to the plane (`faces`): only where
at least two mouths agree on the plane, and only if no view's outline loses area by it.

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
from agentic_cad import regions, spans, tone, trace, verify
from agentic_cad import visual_hull as vh

ENABLED = os.environ.get("HULL_MOUTHS", "1") != "0"
TIMEOUT = float(os.environ.get("HULL_MOUTHS_TIMEOUT", 40))
ON_LINES = 0.75      # share of the end's circle that must lie on lines of the view
EXPLAINED = 0.5      # ... and at most this share on edges our own solid draws there
FACING = 0.5         # the view looks at the end at least this squarely (cosine)
MIN_PX = 8           # a circle smaller than this in the view is not judged
BURIED_PX = 2.0      # material beyond the end, in pixels, before it counts as buried
VIEWS = verify.ORTHO_VIEWS + ("iso_front_right_top", "iso_back_left_bottom")
FACES = os.environ.get("HULL_MOUTH_FACES", "1") != "0"
AROUND_PX = 4        # the face is the area this far outside the mouth's circle
GROW_PX = 2          # an area ends where its lines begin; the face goes on to their middle
MIN_MOUTHS = 2       # mouths that must lie on one plane in one area before the area is taken for their face
MASK_SLACK = 0.0005  # a face cut may uncover at most this share of a view's outline area
TONE_FITS = 10       # the area's tone is within this many levels of what the tone table gives the face


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
                if share < ON_LINES or (best is not None and (share <= best[0] or best[1] in verify.ORTHO_VIEWS)
                                        and not (name in verify.ORTHO_VIEWS and best[1] not in verify.ORTHO_VIEWS)):
                    continue  # the best circle; a straight view before an isometric one (it also gives the face)
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


def faces(mouths: list[dict], observed: dict, dims: dict[str, float], table) -> list[dict]:
    """The faces the mouths lie on. A hole is made from a face, so the plane through its mouth,
    square to its axis, is that face; in the view that draws the mouth, the area between the lines
    right around the mouth is the face as far as it shows. Taken only where that area has one even
    tone and it is the tone a face of that direction has in that view. Mouths of straight views."""
    areas = regions.find(observed)
    found = {}
    for mouth in mouths:
        name = mouth["view"]
        if name not in areas:
            continue
        view = observed[name]
        labels, listed = areas[name]
        end, outward = np.array(mouth["end"]), np.array(mouth["outward"])
        # the area just outside the circle: a few pixels beyond it, all around
        reach = mouth["radius"] + AROUND_PX * view.mm_per_px
        ring = spans._pixels(spans._circle(end, outward, reach, 72), view, dims).round().astype(int)
        ring = ring[(ring.min(axis=1) >= 0) & (ring.max(axis=1) < labels.shape[0])]
        around = labels[ring[:, 1], ring[:, 0]]
        around = around[around > 0]
        if len(around) < 24:
            continue
        label = int(np.bincount(around).argmax())
        if (around == label).mean() < 0.6:
            continue  # the mouth sits on a boundary between areas: no one face around it
        region = next((r for r in listed if r.label == label), None)
        if region is None or region.kind == "curved":
            continue
        expected = float(table.tone(tone.in_camera(outward, name)))
        if abs(expected - region.tone) > TONE_FITS:
            continue
        key = (name, label, tuple(np.round(outward, 3)), round(float(end @ outward) / (2 * view.mm_per_px)))
        entry = found.setdefault(key, {"view": name, "label": label, "point": mouth["end"], "outward": mouth["outward"],
                                       "towards": mouth["towards"], "reach": mouth["reach"], "mouths": 0,
                                       "area_px": region.area, "tone": region.tone, "tone_expected": round(expected)})
        entry["mouths"] += 1
    result = []
    for entry in found.values():
        if entry["mouths"] < MIN_MOUTHS:
            continue  # one mouth can lie at the bottom of a recess too narrow to draw (practice 7)
        view = observed[entry["view"]]
        labels = areas[entry["view"]][0]
        # out to the middle of the lines around the area
        mask = cv2.dilate((labels == entry["label"]).astype(np.uint8), np.ones((2 * GROW_PX + 1, 2 * GROW_PX + 1), np.uint8))
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        right, up, towards = verify.camera(entry["view"])
        centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
        point, outward, half = np.array(entry["point"]), np.array(entry["outward"]), labels.shape[0] / 2
        loops = []
        for contour, (_, _, _, parent) in zip(contours, hierarchy[0] if hierarchy is not None else []):
            polygon = cv2.approxPolyDP(contour, 1.0, True).reshape(-1, 2).astype(float)
            if len(polygon) < 3 or cv2.contourArea(contour) < 30:
                continue
            flat = (centre + np.outer((polygon[:, 0] - half + 0.5) * view.mm_per_px, right)
                    + np.outer((half - 0.5 - polygon[:, 1]) * view.mm_per_px, up))
            # along the line of sight onto the plane of the face
            onto = ((point - flat) @ outward) / float(towards @ outward)
            loops.append({"hole": parent >= 0, "points": (flat + np.outer(onto, towards)).tolist()})
        if any(not loop["hole"] for loop in loops):
            result.append({**entry, "loops": loops})
    return result


def _missing(solid: TopoDS_Shape, observed: dict, dims: dict[str, float]) -> dict[str, float]:
    """Per view, the share of the part's outline area that the solid does not cover, a few pixels
    along the outline forgiven. A cut that makes it grow has taken material the view shows."""
    mesh = verify.tessellate(solid, 0.0005 * max(dims.values()), 0.2)
    kernel = np.ones((2 * vh.CHECK_BAND_PX + 1, 2 * vh.CHECK_BAND_PX + 1), np.uint8)
    result = {}
    for name, view in observed.items():
        drawing = verify.draw(mesh, name, dims, view.mm_per_px)
        inner = cv2.erode(view.mask.astype(np.uint8), kernel) > 0
        result[name] = float((inner & ~drawing.mask).sum()) / max(1, int(view.mask.sum()))
    return result


def _prism(points: list, sweep: np.ndarray) -> TopoDS_Shape | None:
    wire = BRepBuilderAPI_MakeWire()
    corners = [gp_Pnt(*map(float, p)) for p in points]
    for first, second in zip(corners, corners[1:] + corners[:1]):
        if first.Distance(second) > 1e-6:
            wire.Add(BRepBuilderAPI_MakeEdge(first, second).Edge())
    if not wire.IsDone():
        return None
    face = BRepBuilderAPI_MakeFace(wire.Wire(), True)
    if not face.IsDone():
        return None
    return BRepPrimAPI_MakePrism(face.Face(), gp_Vec(*map(float, sweep))).Shape()


def cut_face(shape: TopoDS_Shape, face: dict) -> TopoDS_Shape | None:
    """Remove from `shape` what lies between a face (faces()) and its camera, islands left standing."""
    sweep = np.array(face["towards"]) * face["reach"]
    tools = List_TopoDS_Shape()
    for loop in face["loops"]:
        if loop["hole"]:
            continue
        tool = _prism(loop["points"], sweep)
        if tool is not None:
            tools.Append(tool)
    if tools.Size() == 0:
        return None
    # what stands inside the area (another area of the view) stays: the islands are put back
    back = sweep * -1e-3
    for loop in face["loops"]:
        island = _prism((np.array(loop["points"]) + back).tolist(), sweep * 1.01) if loop["hole"] else None
        if island is None:
            continue
        trimmed = List_TopoDS_Shape()
        for tool in tools:
            trimmed.Append(BRepAlgoAPI_Cut(tool, island).Shape())
        tools = trimmed
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
        if FACES:
            carved = _cut_faces(agent, step, carved, mouths, observed, dims, target)
        agent._record(step, carved, "mouths")
        return carved


def _cut_faces(agent, step, solid, mouths: list[dict], observed: dict, dims: dict, target):
    try:
        mesh = verify.tessellate(solid, 0.0005 * max(dims.values()), 0.2)
        table = tone.choose(observed, {name: verify.draw(mesh, name, dims, view.mm_per_px)
                                       for name, view in observed.items()}) or tone.tables()["organisers"]
        found = faces(mouths, observed, dims, table)
    except Exception as error:
        step.log(f"looking for the faces of the mouths raised {error!r}; only the mouths are opened", level="warning")
        return solid
    step.metric(faces=len(found))
    if found:
        step.attach("mouth_faces.json", [{k: v for k, v in face.items() if k != "loops"} for face in found])
    kept = 0
    missing = _missing(solid, observed, dims)
    for face in found:
        attempt = agent._guarded(step, solid, target, lambda: agent.cutter.run(cut_face, solid, face, TIMEOUT), quiet=True)
        if attempt is None or vh.volume(attempt) >= vh.volume(solid):
            continue
        after = _missing(attempt, observed, dims)
        worse = [name for name in after if after[name] - missing.get(name, 0.0) > MASK_SLACK]
        if worse:
            step.log(f"the face in {face['view']} would take the part out of the outline of {worse}; not kept", level="warning")
            continue
        solid, kept, missing = attempt, kept + 1, after
    step.metric(faces_cut=kept)
    return solid
