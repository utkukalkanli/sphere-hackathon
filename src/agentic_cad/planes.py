"""Deterministic: flat faces whose plane the isometric views give (sources.py).

An isometric view shows faces square to an axis in the tone of their side, and for the large ones
the plane they lie in can be read off the pictures alone: the level at which the face, carried
into the straight view that looks at it, falls inside one area there with its lines on that view's
lines (`sources.faces`, "sure"). That area of the straight view is one flat face looking at its
camera, so all of it lies in that plane. Where our solid stands in front of the plane over the
area, the column is cut down to it.

This is the depth the level-faces step cannot have (it only spreads levels our solid already
has) and the shading step looks for from the other side (it slides an area of the straight view
and asks the isometric view). Here the isometric face comes first, so a floor of which only a
part is in sight still gives its level.

A level is used only if nothing contradicts it: the solid, which contains the part, must not lie
under the plane anywhere over the area, and two faces that land in one area must agree. Each cut
is kept only if it leaves the part's volume and the verifier does not rate the result lower.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import depth as level_faces
from agentic_cad import regions, sources, trace, verify
from agentic_cad import visual_hull as vh

LEVEL_PX = 1.5         # depths closer than this many pixels are the same level (as in depth.py)
LEVEL_ERROR_PX = 4     # a level read off the pictures is right to 3 px: the solid must stand further in front than that
MIN_ABOVE_PX = 40      # the solid must stand in front of the plane over this many pixels of the area
MIN_ABOVE_SHARE = 0.05 # ... and over this share of it
MAX_BELOW_SHARE = 0.05 # share of the area where the solid may lie under the plane: more, and it is not the area's plane
MIN_IN_AREA = 0.85     # share of the face that must land in the one area
SCORE_SLACK = 0.003    # the verifier's score may drop by this much through a cut and still pass
MAX_CUTS = 12          # per case: the largest areas first


def find(observed: dict, dims: dict[str, float], found: dict, drawings, facing: int | None) -> tuple[list[vh.Silhouette], list[str]]:
    """The cuts the sure faces of the isometric views ask for, largest area first, and why others were left."""
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    reach = 2 * float(np.linalg.norm(centre))
    wanted: dict[tuple[str, int], list[float]] = {}   # (straight view, area) -> depths its faces give
    areas: dict[str, tuple[np.ndarray, list[int]]] = {}
    notes = []
    for view, (labels, listed) in found.items():
        for face in listed:
            if not face.sure or face.straight not in observed:
                continue
            if face.straight not in areas:
                areas[face.straight] = level_faces.flat_areas(observed[face.straight], facing)
            grown, flat = areas[face.straight]
            pixels = sources.footprint(face, labels, observed, dims)
            if not len(pixels):
                continue
            hit = np.bincount(grown[pixels[:, 1], pixels[:, 0]])
            hit[0] = 0
            area = int(hit.argmax())
            if area not in flat or hit[area] < MIN_IN_AREA * len(pixels):
                notes.append(f"{'xyz'[face.axis]} = {face.level:.1f} ({face.view}): lands in no single flat area of {face.straight}")
                continue
            # mm towards the straight camera from the centre of the box: that camera looks along the axis
            wanted.setdefault((face.straight, area), []).append(face.sign * (face.level - centre[face.axis]))
    cuts = []
    for (name, area), depths in wanted.items():
        view, drawing = observed[name], drawings[name]
        tolerance = LEVEL_PX * view.mm_per_px
        if max(depths) - min(depths) > 2 * tolerance:
            notes.append(f"{name} area {area}: its faces disagree ({min(depths):.1f} and {max(depths):.1f} mm)")
            continue
        level = float(np.median(depths))
        grown, _ = areas[name]
        whole = grown == area
        core = cv2.erode(whole.astype(np.uint8), np.ones((2 * level_faces.EDGE_PX + 1,) * 2, np.uint8)) > 0
        inside = core & drawing.mask
        if not inside.any():
            continue
        surface = drawing.depth[inside]
        # a face our solid has a pixel or two off is not cut again: that would only shave it (practice 1)
        above, below = surface > level + LEVEL_ERROR_PX * view.mm_per_px, surface < level - LEVEL_ERROR_PX * view.mm_per_px
        if below.mean() > MAX_BELOW_SHARE:
            notes.append(f"{name} area {area}: the solid lies under {level:.1f} mm over {below.mean():.0%} of it; not its plane")
            continue
        if above.sum() < MIN_ABOVE_PX or above.mean() < MIN_ABOVE_SHARE:
            continue   # the solid already has the face there
        right, up, towards = verify.camera(name)
        half = view.mask.shape[0] / 2

        def to_mm(points: np.ndarray, pixel=view.mm_per_px, half=half) -> np.ndarray:
            return np.column_stack([(points[:, 0] + 0.5 - half) * pixel, (half - 0.5 - points[:, 1]) * pixel])

        outlines = vh.trace_outlines(whole.astype(np.uint8) * 255, to_mm, view.mm_per_px)
        if not outlines:
            continue
        cuts.append(vh.Silhouette(name=f"plane_{name}_{area}", views=[name], mask=whole.astype(np.uint8) * 255,
                                  outlines=outlines, origin=centre + level * towards, right=right, up=up,
                                  direction=towards, depth=reach,
                                  info={"level": round(level, 3), "pixels": int(whole.sum()), "view": name,
                                        "above_px": int(above.sum()),
                                        "drop_mm": round(float(np.median(surface[above]) - level), 2)}))
    cuts.sort(key=lambda cut: -cut.info["pixels"])
    return cuts[:MAX_CUTS], notes


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step: cut the areas whose plane the isometric views give down to it."""
    with trace.step("plane_faces", kind="code") as step:
        if case_dir is None:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        evidence = agent.evidence(case_dir, dims)
        observed = evidence.observed
        found = evidence.iso_faces(solid)
        sure = sum(1 for _, listed in found.values() for face in listed if face.sure)
        step.metric(sure_faces=sure)
        if not sure:
            step.log("the isometric views give no face a sure plane")
            return solid
        straight = [name for name in verify.ORTHO_VIEWS if name in observed]
        facing = regions.facing_tone({name: regions._measure(observed[name])[1] for name in straight})
        cuts, notes = find(observed, dims, found, evidence.drawings(solid), facing)
        for note in notes[:8]:
            step.log(note)
        if not cuts:
            step.log(f"{sure} face(s) with a sure plane; the solid has all of them")
            return solid
        score = verify.check(solid, case_dir, observed).score
        kept = 0
        for cut in cuts:
            if agent.out_of_time():
                step.log("out of time for this case; the remaining planes are not cut", level="warning")
                break
            text = f"{cut.info['view']}: {cut.info['pixels']} px down {cut.info['drop_mm']} mm to {cut.info['level']} mm"
            current = solid
            carved = agent._guarded(step, current, target,
                                    lambda: agent.cutter.run(level_faces.remove, current, [cut], level_faces.TIMEOUT), quiet=True)
            if carved is None or vh.volume(carved) >= vh.volume(current) * (1 - 1e-6):
                step.log(f"{text}: not cut")
                continue
            after = verify.check(carved, case_dir, observed).score
            if after < score - SCORE_SLACK:
                step.log(f"{text}: the verifier rates the result lower ({score:.3f} to {after:.3f}); not kept", level="warning")
                continue
            step.log(f"{text}: kept ({score:.3f} to {after:.3f})")
            solid, score, kept = carved, after, kept + 1
        step.metric(planes_cut=kept)
        if kept == 0:
            return solid
        step._status = None
        agent._record(step, solid, "planes")
        return solid
