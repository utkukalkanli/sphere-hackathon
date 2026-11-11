"""Step 4, deterministic: give a flat face one depth.

The views draw a dark line on every face boundary, so the area between the lines of a straight view
is one face. If that area has one flat tone, the face is a plane, and if the tone is the one of faces
looking straight at the camera, the whole face lies at one depth.

The solid carved so far contains the part, so in that view it is never lower than the part. Where
such a face covers several levels of our solid (a plate that our solid has right in one place and
buried under a block in another), the lowest of those levels is the highest the face can be: the
whole area is cut down to it.

This needs no depth from anywhere else: it only spreads a level our solid already has over the face
the views say it belongs to. Recesses that our solid does not reach anywhere (blind pockets) are not
found here. Each view's cuts are kept only if the verifier does not rate the result lower.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad import trace
from agentic_cad import verify
from agentic_cad import visual_hull as vh

MIN_AREA_PX = 150        # smaller areas between lines are not looked at
EDGE_PX = 3              # this close to its lines an area is not sampled: views and solid differ by a pixel
TONE_SPREAD = 3.0        # a flat tone varies less than this (0..255) per colour channel
TONE_MATCH = 5.0         # ... and is this close to the tone of the faces that look at the camera
LEVEL_PX = 1.5           # depths closer than this many pixels are the same level
MIN_LEVEL_PX = 40        # a level counts when our solid has it flat over this many pixels of the area
MIN_LEVEL_SHARE = 0.05   # ... and over this share of the area
FACING = 0.9995          # cosine: our solid's face looks straight at the camera
TIMEOUT = float(os.environ.get("HULL_DEPTH_TIMEOUT", 30))
SCORE_SLACK = 0.003      # the verifier's score may drop by this much through a cut and still pass
MAX_SINGLE = 6           # when a view's cuts are refused together, this many are tried one by one


def facing_tone(view: verify.Observed) -> np.ndarray | None:
    """The tone of faces that look straight at the camera: the most common colour of the part. In a
    straight view of a machined part most of what is seen are such faces."""
    pixels = view.image[view.mask & ~view.lines]
    if len(pixels) < MIN_AREA_PX:
        return None
    colours, counts = np.unique(pixels.reshape(-1, 3), axis=0, return_counts=True)
    return colours[counts.argmax()].astype(float)


def flat_areas(view: verify.Observed) -> tuple[np.ndarray, list[int]]:
    """Label image of the areas between the lines of a view (0 = none), each grown up to the middle
    of the lines around it, and the labels of those that are flat faces looking at the camera."""
    tone = facing_tone(view)
    free = (view.mask & ~view.lines).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    if tone is None or count <= 1:
        return np.zeros(view.mask.shape, np.int32), []
    chosen = []
    core = cv2.erode(free, np.ones((2 * EDGE_PX + 1, 2 * EDGE_PX + 1), np.uint8)) > 0
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < MIN_AREA_PX:
            continue
        pixels = view.image[(labels == label) & core].astype(float)
        if len(pixels) < MIN_AREA_PX / 3:
            continue
        if pixels.std(axis=0).max() <= TONE_SPREAD and np.abs(pixels.mean(axis=0) - tone).max() <= TONE_MATCH:
            chosen.append(label)
    # every line pixel goes to the nearest area, so that neighbouring areas meet in the middle of a line
    distance, nearest = cv2.distanceTransformWithLabels(1 - free, cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
    lookup = np.zeros(int(nearest.max()) + 1, np.int32)
    lookup[nearest[free > 0]] = labels[free > 0]
    grown = np.where((distance <= 4) & cv2.dilate(view.mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool),
                     lookup[nearest], 0)
    return grown, chosen


def lowest_level(depth: np.ndarray, facing: np.ndarray, tolerance: float) -> float | None:
    """The lowest depth our solid has as a flat level over a real part of these pixels, if it also has
    higher ones."""
    flat = np.sort(depth[facing])
    if len(flat) < MIN_LEVEL_PX:
        return None
    # the lowest stretch of MIN_LEVEL_PX sorted depths that all lie within the tolerance
    spread = flat[MIN_LEVEL_PX - 1:] - flat[:len(flat) - MIN_LEVEL_PX + 1]
    start = np.nonzero(spread <= tolerance)[0]
    if not len(start):
        return None
    level = float(np.median(flat[start[0]:start[0] + MIN_LEVEL_PX]))
    if (np.abs(flat - level) <= tolerance).sum() < MIN_LEVEL_SHARE * len(depth):
        return None  # a sliver at that depth (a hole of ours seen past the edge of the face) is no evidence
    if (depth > level + tolerance).sum() < MIN_LEVEL_PX:
        return None  # nothing stands above it
    return level


def find(view: verify.Observed, drawing: verify.Drawing, dims: dict[str, float]) -> list[vh.Silhouette]:
    """What to cut away in one view: for every flat face that covers several levels of our solid, the
    face's area from its lowest level towards the camera."""
    right, up, towards = verify.camera(view.name)
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    half = view.mask.shape[0] / 2
    tolerance = LEVEL_PX * view.mm_per_px
    reach = 2 * float(np.linalg.norm(centre))
    labels, chosen = flat_areas(view)
    if not chosen:
        return []
    core = cv2.erode((labels > 0).astype(np.uint8), np.ones((2 * EDGE_PX + 1, 2 * EDGE_PX + 1), np.uint8)) > 0
    core &= drawing.mask
    facing = drawing.normal @ towards >= FACING

    def to_mm(pixels: np.ndarray) -> np.ndarray:
        return np.column_stack([(pixels[:, 0] + 0.5 - half) * view.mm_per_px, (half - 0.5 - pixels[:, 1]) * view.mm_per_px])

    cuts = []
    for label in chosen:
        area = labels == label
        inside = area & core
        # lines our own solid would draw inside the area are fine; its depth is what counts
        level = lowest_level(drawing.depth[inside], facing[inside], tolerance)
        if level is None:
            continue
        outlines = vh.trace_outlines(area.astype(np.uint8) * 255, to_mm, view.mm_per_px)
        if not outlines:
            continue
        cuts.append(vh.Silhouette(name=f"area_{label}", views=[view.name], mask=area.astype(np.uint8) * 255,
                                  outlines=outlines, origin=centre + level * towards, right=right, up=up,
                                  direction=towards, depth=reach,
                                  info={"level": round(level, 3), "pixels": int(area.sum())}))
    return cuts


def remove(shape: TopoDS_Shape, cuts: list[vh.Silhouette]) -> TopoDS_Shape | None:
    """`shape` without the prisms of `cuts`. One valid solid, or None. Runs in a child process."""
    tools = List_TopoDS_Shape()
    for cut in cuts:
        tools.Append(vh.prism(cut))
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


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step: view by view, level the flat faces and keep what the verifier accepts."""
    with trace.step("level_faces", kind="code") as step:
        if case_dir is None:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        observed = verify.observe(case_dir, dims)
        straight = [name for name in verify.ORTHO_VIEWS if name in observed]
        if not straight:
            step.log("no straight view could be read")
            return solid
        start = solid
        score = None
        deflection = 0.3 * min(view.mm_per_px for view in observed.values())
        levelled = 0

        def attempt(current, cuts):
            nonlocal score
            carved = agent._guarded(step, current, target, lambda: agent.cutter.run(remove, current, cuts, TIMEOUT), quiet=True)
            if carved is None or vh.volume(carved) >= vh.volume(current) * (1 - 1e-6):
                return None
            if score is None:
                score = verify.check(start, case_dir, observed).score
            after = verify.check(carved, case_dir, observed).score
            if after < score - SCORE_SLACK:
                step.log(f"the verifier rates the result lower ({score:.3f} to {after:.3f}); not kept", level="warning")
                return None
            score = after
            return carved

        for name in straight:
            if agent.out_of_time():
                step.log("out of time for this case; the remaining views are not looked at", level="warning")
                break
            drawing = verify.draw(verify.tessellate(solid, deflection), name, dims, observed[name].mm_per_px)
            cuts = find(observed[name], drawing, dims)
            if not cuts:
                continue
            step.log(f"{name}: {len(cuts)} flat face(s) cover several levels: "
                     + ", ".join(f"{c.info['pixels']} px to {c.info['level']} mm" for c in cuts[:8]))
            carved = attempt(solid, cuts)
            if carved is not None:
                solid, levelled = carved, levelled + len(cuts)
            elif len(cuts) > 1:
                for cut in sorted(cuts, key=lambda c: -c.info["pixels"])[:MAX_SINGLE]:
                    carved = attempt(solid, [cut])
                    if carved is not None:
                        solid, levelled = carved, levelled + 1
        step.metric(faces_levelled=levelled)
        if levelled == 0:
            step.log("no flat face covers more than one level of the solid")
            return solid
        step._status = None
        agent._record(step, solid, "depth")
        return solid
