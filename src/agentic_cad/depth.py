"""Step 4, deterministic: give a flat face one depth.

The views draw a dark line on every face boundary, so the area between the lines of a straight view
is one face. If that area has one flat tone, the face is a plane, and if the tone is the one of faces
looking straight at the camera, the whole face lies at one depth.

The solid carved so far contains the part, so in that view it is never lower than the part. Where
such a face covers several levels of our solid (a plate that our solid has right in one place and
buried under a block in another), the lowest of those levels is the highest the face can be: the
whole area is cut down to it.

Where the solid has no flat level under such a face at all (the flat top of a flange between fins,
under a cone the side views left: practice part 6), the same argument gives the lowest point of the
solid's surface over the area (`support_level`).

This needs no depth from anywhere else: it only spreads a level our solid already has over the face
the views say it belongs to. Recesses that our solid does not reach anywhere (blind pockets) are not
found here. Each view's cuts are kept only if the verifier does not rate the result lower.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad import regions
from agentic_cad import tone
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
UNEXPLAINED = 12.0       # grey levels: the solid's surface does not explain an area's tones if they are this far off (median)
MIN_EDGE_ON = 0.9        # share of a face's edge-on stretch in a view at right angles that must lie on a line
MIN_RECALL_GAIN = 0.01   # share of the views' lines a supported cut must newly explain to be kept on that alone
MIN_SOLIDITY = 0.9       # a slanted face's area fills this share of its convex outline
MIN_EDGE_PX = 12         # a slanted face hangs on a straight edge at least this long
MIN_DROP_PX = 2.0        # ... and is cut only if that lowers the solid by this much on average
MIN_STRETCH_PX = 10      # shorter stretches say nothing
ONE_PLANE_DEG = 3.0      # our solid's surface over an area is one plane if its normals stay within this
SUPPORT = os.environ.get("HULL_DEPTH_SUPPORT", "1") != "0"   # also level faces our solid has no flat level under
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


def flat_areas(view: verify.Observed, facing: int | None = None) -> tuple[np.ndarray, list[int]]:
    """Label image of the areas between the lines of a view (0 = none), each grown up to the middle
    of the lines around it, and the labels of those that are flat faces looking at the camera.
    `facing` is the facing tone of all six views (regions.facing_tone): an area that has it evenly
    counts too. The real renders light a flat face a little unevenly, so that a face at the rim of
    the image misses this view's commonest colour by more than TONE_MATCH (practice part 6, top)."""
    tone = facing_tone(view)
    # the areas of regions.py: split at the lines, and also where the tone jumps without a line found
    free = (regions.segment(view)[0] > 0).astype(np.uint8)
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
        elif facing is not None:
            low, middle, high = np.percentile(pixels.max(axis=1), (8, 50, 92))
            if high - low <= regions.FLAT_SPREAD and abs(middle - facing) <= regions.FACING_TOLERANCE:
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


def support_level(depth: np.ndarray, normal: np.ndarray, tolerance: float, misfit: float | None) -> float | None:
    """The level a flat face square to the camera can be cut down to where our solid has no flat level
    under it at all (a flat face that the solid covers with a cone or a slope).

    The solid contains the part, so it is nowhere lower than the face; the face is one level; so the
    face is no higher than the lowest point of the solid over it. Not the very lowest is taken but the
    level a twentieth of the area lies under, as with the flat levels above: a sliver may be something
    else.

    All of that needs the area to be a face square to the camera, and its tone does not prove it:
    other directions have that tone too (the fin of practice part 6 that looks at the side camera, a
    cone patch 22 degrees off, was sliced flat by a first version of this). So nothing is cut where
    the solid's own surface already explains the tones of the area: `misfit` is the median difference
    between the area's tones and those the renderer gives the solid's surface there (tone.py), and it
    must be large. Nor where the solid's surface over the area is one plane. The caller also wants a
    line where the face is seen edge-on (`seen_edge_on`)."""
    if len(depth) < MIN_LEVEL_PX / MIN_LEVEL_SHARE or misfit is None or misfit < UNEXPLAINED:
        return None
    mean = normal.mean(axis=0)
    mean /= max(float(np.linalg.norm(mean)), 1e-9)
    if np.percentile(np.degrees(np.arccos(np.clip(normal @ mean, -1, 1))), 90) <= ONE_PLANE_DEG:
        return None
    level = float(np.percentile(depth, 100 * MIN_LEVEL_SHARE))
    if (depth > level + tolerance).mean() < 0.5:
        return None  # most of the area is already about there
    return level


_NEAR_LINE: dict = {}   # view -> pixels within the tolerance of one of its lines


def seen_edge_on(area: np.ndarray, level: float, view: verify.Observed, observed: dict, dims: dict[str, float]) -> float:
    """A face square to this camera is seen edge-on by the cameras at right angles to it: there the
    whole face is one straight stretch, and where it shows, the view draws a line or the part's outline
    along it. Returns the largest share of that stretch that lies on a line, over those views."""
    right, up, towards = verify.camera(view.name)
    rows, cols = np.nonzero(area)
    half = view.mask.shape[0] / 2
    points = (np.outer((cols + 0.5 - half) * view.mm_per_px, right) + np.outer((half - 0.5 - rows) * view.mm_per_px, up)
              + level * towards)
    best = 0.0
    for name, other in observed.items():
        if name not in verify.ORTHO_VIEWS or abs(verify.camera(name)[2] @ towards) > 0.1:
            continue
        other_right, other_up, _ = verify.camera(name)
        size = other.mask.shape[0]
        c = np.rint(points @ other_right / other.mm_per_px + size / 2 - 0.5).astype(int)
        r = np.rint(size / 2 - 0.5 - points @ other_up / other.mm_per_px).astype(int)
        ok = (c >= 0) & (c < size) & (r >= 0) & (r < size)
        stretch = np.unique(r[ok] * size + c[ok])
        if len(stretch) < MIN_STRETCH_PX:
            continue
        if id(other) not in _NEAR_LINE:   # one case at a time: the views of the previous one are dropped
            if len(_NEAR_LINE) > 16:
                _NEAR_LINE.clear()
            _NEAR_LINE[id(other)] = (other, cv2.distanceTransform((~other.lines).astype(np.uint8), cv2.DIST_L2, 3) <= verify.TOLERANCE_PX)
        best = max(best, float(_NEAR_LINE[id(other)][1].ravel()[stretch].mean()))
    return best


def find(view: verify.Observed, drawing: verify.Drawing, dims: dict[str, float], table=None,
         observed: dict | None = None, facing: int | None = None) -> list[vh.Silhouette]:
    """What to cut away in one view: for every flat face that covers several levels of our solid, the
    face's area from its lowest level towards the camera."""
    right, up, towards = verify.camera(view.name)
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    half = view.mask.shape[0] / 2
    tolerance = LEVEL_PX * view.mm_per_px
    reach = 2 * float(np.linalg.norm(centre))
    labels, chosen = flat_areas(view, facing)
    if not chosen:
        return []
    core = cv2.erode((labels > 0).astype(np.uint8), np.ones((2 * EDGE_PX + 1, 2 * EDGE_PX + 1), np.uint8)) > 0
    core &= drawing.mask
    facing = drawing.normal @ towards >= FACING

    def to_mm(pixels: np.ndarray) -> np.ndarray:
        return np.column_stack([(pixels[:, 0] + 0.5 - half) * view.mm_per_px, (half - 0.5 - pixels[:, 1]) * view.mm_per_px])

    cuts = []
    surface = np.where(drawing.mask, drawing.depth, np.nan)   # the solid's surface as it will be after the cuts
    levels: dict[int, float] = {}                              # flat faces square to the camera whose level is known
    for label in chosen:
        area = labels == label
        inside = area & core
        if not inside.any():
            continue
        # lines our own solid would draw inside the area are fine; its depth is what counts
        level = lowest_level(drawing.depth[inside], facing[inside], tolerance)
        supported = False
        if level is None and SUPPORT and table is not None and observed is not None:
            chosen_table = table() if callable(table) else table
            misfit = None
            if chosen_table is not None:
                expected = chosen_table.tone(tone.in_camera(drawing.normal[inside], view.name))
                misfit = float(np.median(np.abs(view.image[inside].max(axis=1).astype(float) - expected)))
            highest = support_level(drawing.depth[inside], drawing.normal[inside], tolerance, misfit)
            if highest is not None:
                # The tone does not prove the face is square to the camera; a line where the views at
                # right angles see it edge-on does, and says at which level: looked for from the
                # lowest point of the solid over the whole area up to the level found above.
                # The highest level with such a line is taken: lower ones may be other faces.
                whole = drawing.depth[area & drawing.mask]
                for value in np.arange(highest, float(whole.min()) - view.mm_per_px, -view.mm_per_px):
                    if seen_edge_on(inside, float(value), view, observed, dims) >= MIN_EDGE_ON:
                        level, supported = float(value), True
                        break
        if level is None:
            if facing[inside].mean() >= 0.9 and np.ptp(np.percentile(drawing.depth[inside], (5, 95))) <= tolerance:
                levels[label] = float(np.median(drawing.depth[inside]))   # already one level: slanted faces can hang on it
            continue
        outlines = vh.trace_outlines(area.astype(np.uint8) * 255, to_mm, view.mm_per_px)
        if not outlines:
            continue
        levels[label] = level
        surface[area] = np.fmin(surface[area], level)
        cuts.append(vh.Silhouette(name=f"area_{label}", views=[view.name], mask=area.astype(np.uint8) * 255,
                                  outlines=outlines, origin=centre + level * towards, right=right, up=up,
                                  direction=towards, depth=reach,
                                  info={"level": round(level, 3), "pixels": int(area.sum()), "supported": supported}))
    if SUPPORT and levels:
        cuts += slanted_cuts(view, labels, levels, surface, core, dims, to_mm)
    return cuts


def slanted_cuts(view: verify.Observed, labels: np.ndarray, levels: dict[int, float], surface: np.ndarray,
                 core: np.ndarray, dims: dict[str, float], to_mm) -> list[vh.Silhouette]:
    """Flat faces at an angle that stand on a flat face of known level (the flanks of the fins of
    practice part 6, on the flange between them).

    Such a face is a plane through the straight edge it shares with the level face. Of all planes
    through that edge, the solid contains the part's, and the part's cannot be steeper than the
    steepest one that stays inside the solid over the area. So the solid is cut down to that plane:
    like the level faces, with no depth from anywhere else. `surface` is the solid's height per pixel
    with the level faces already lowered."""
    right, up, towards = verify.camera(view.name)
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    top = float(abs(towards) @ np.array([dims["x"], dims["y"], dims["z"]])) / 2
    margin = 0.05 * max(dims.values())
    half = view.mask.shape[0] / 2
    pixel = view.mm_per_px
    tones = view.image.max(axis=2).astype(float)
    kernel = np.ones((3, 3), np.uint8)
    grown = {label: cv2.dilate((labels == label).astype(np.uint8), kernel) > 0 for label in levels}
    cuts = []
    for label in np.unique(labels[labels > 0]):
        if label in levels:
            continue
        area = labels == label
        inside = area & core & np.isfinite(surface)
        if inside.sum() < MIN_AREA_PX:
            continue
        low, high = np.percentile(tones[inside], (8, 92))
        if high - low > regions.FLAT_SPREAD:
            continue   # not one even tone: not a flat face
        # Two faces of the same tone with no line found between them are one area (two flanks either
        # side of a groove). Such an area bends around a corner; one that fills its own convex outline
        # is taken for one face.
        contours, _ = cv2.findContours(area.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours or area.sum() < MIN_SOLIDITY * cv2.contourArea(cv2.convexHull(max(contours, key=cv2.contourArea))):
            continue
        rows, cols = np.nonzero(inside)
        a, b = (cols + 0.5 - half) * pixel, (half - 0.5 - rows) * pixel
        best = None
        for neighbour, level in levels.items():
            edge_rows, edge_cols = np.nonzero(area & grown[neighbour])
            if len(edge_rows) < MIN_EDGE_PX:
                continue
            points = np.column_stack([(edge_cols + 0.5 - half) * pixel, (half - 0.5 - edge_rows) * pixel])
            middle = points.mean(axis=0)
            _, _, axes = np.linalg.svd(points - middle)
            along, across = axes[0], axes[1]
            if np.abs((points - middle) @ across).max() > 2.0 * pixel or np.ptp((points - middle) @ along) < MIN_EDGE_PX * pixel:
                continue   # not a straight edge
            if (np.column_stack([a, b]).mean(axis=0) - middle) @ across < 0:
                across = -across   # now pointing into the area
            reach = (a - middle[0]) * across[0] + (b - middle[1]) * across[1]
            far = reach >= 3 * pixel
            if far.sum() < MIN_AREA_PX / 3:
                continue
            # the steepest plane through the edge under the solid's surface: as with the levels, a
            # twentieth of the area may lie under it
            slope = float(np.percentile((surface[rows, cols][far] - level) / reach[far], 100 * MIN_LEVEL_SHARE))
            height = level + slope * reach
            removes = float(np.clip(surface[rows, cols] - height, 0, None).sum()) * pixel ** 2
            if removes / (len(rows) * pixel ** 2) < MIN_DROP_PX * pixel:
                continue
            if best is None or removes > best[0]:
                best = (removes, middle, across, slope, level)
        if best is None:
            continue
        removes, middle, across, slope, level = best
        outlines = vh.trace_outlines(area.astype(np.uint8) * 255, to_mm, pixel)
        if not outlines:
            continue
        point = centre + middle[0] * right + middle[1] * up + level * towards
        normal = towards - slope * (across[0] * right + across[1] * up)
        cuts.append(vh.Silhouette(name=f"slanted_{label}", views=[view.name], mask=area.astype(np.uint8) * 255,
                                  outlines=outlines, origin=centre + (top + margin) * towards, right=right, up=up,
                                  direction=-towards, depth=float(2 * (top + margin)),
                                  info={"plane": (tuple(map(float, point)), tuple(map(float, normal / np.linalg.norm(normal)))),
                                        "level": round(level, 3), "pixels": int(area.sum()), "supported": True,
                                        "slope_deg": round(float(np.degrees(np.arctan(slope))), 1)}))
    return cuts


def _wedge(cut: vh.Silhouette) -> TopoDS_Shape | None:
    """The column over an area, from its slanted plane towards the camera."""
    column = vh.prism(cut)
    point, normal = cut.info["plane"]
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*point), gp_Dir(*normal))).Face()
    above = BRepPrimAPI_MakeHalfSpace(face, gp_Pnt(*(np.array(point) + 1e3 * np.array(normal)))).Solid()
    common = BRepAlgoAPI_Common(column, above)
    return common.Shape() if common.IsDone() else None


def remove(shape: TopoDS_Shape, cuts: list[vh.Silhouette]) -> TopoDS_Shape | None:
    """`shape` without the prisms of `cuts`. One valid solid, or None. Runs in a child process."""
    tools = List_TopoDS_Shape()
    for cut in cuts:
        tool = _wedge(cut) if cut.info.get("plane") else vh.prism(cut)
        if tool is not None:
            tools.Append(tool)
    if tools.Size() == 0:
        return None
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
        score = recall = None
        deflection = 0.3 * min(view.mm_per_px for view in observed.values())
        levelled = 0
        chosen: list = []
        facing = regions.facing_tone({name: regions._measure(observed[name])[1] for name in straight})

        def attempt(current, cuts):
            nonlocal score, recall
            carved = agent._guarded(step, current, target, lambda: agent.cutter.run(remove, current, cuts, TIMEOUT), quiet=True)
            if carved is None or vh.volume(carved) >= vh.volume(current) * (1 - 1e-6):
                return None
            if score is None:
                first = verify.check(start, case_dir, observed)
                score, recall = first.score, first.mean("recall")
            report = verify.check(carved, case_dir, observed)
            after = report.score
            # A face cut down to the solid's own lowest point leaves walls where its neighbours are
            # still to be cut, and the verifier counts their edges against it. What such a cut must do
            # instead: make more of the views' lines boundaries of the solid, and leave the outline alone.
            supported = any(c.info.get("supported") for c in cuts) and report.mean("recall") >= recall + MIN_RECALL_GAIN \
                and report.mean("silhouette") >= verify.check(current, case_dir, observed).mean("silhouette") - 1e-4
            if after < score - SCORE_SLACK and not supported:
                step.log(f"the verifier rates the result lower ({score:.3f} to {after:.3f}); not kept", level="warning")
                return None
            score, recall = after, report.mean("recall")
            return carved

        for name in straight:
            if agent.out_of_time():
                step.log("out of time for this case; the remaining views are not looked at", level="warning")
                break
            mesh = verify.tessellate(solid, deflection)
            drawing = verify.draw(mesh, name, dims, observed[name].mm_per_px)

            def table(mesh=mesh):   # which renderer's tones: asked for only when a face needs it
                if not chosen:
                    chosen.append(tone.choose(observed, {n: verify.draw(mesh, n, dims, o.mm_per_px) for n, o in observed.items()}))
                return chosen[0]

            cuts = find(observed[name], drawing, dims, table, observed, facing)
            if not cuts:
                continue
            slanted = sum(1 for c in cuts if c.info.get("plane"))
            step.log(f"{name}: {len(cuts) - slanted} flat face(s) to level, {slanted} slanted face(s) standing on one: "
                     + ", ".join(f"{c.info['pixels']} px to {c.info['level']} mm" + (f" at {c.info['slope_deg']} deg" if c.info.get("plane") else "")
                                 for c in cuts[:8]))
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
