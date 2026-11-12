"""A recess around a listed hole's mouth: a view draws a round line about the hole, our solid is flat there.

facts.json gives every hole's ends exactly. Where a hole leaves the part into a recess (a port bowl,
a counterbore or countersink the list does not carry as a full cylinder), the straight view along the
hole's axis draws the recess's rim as a closed line about the hole, centred on it, and the hole's
end lies under our surface by the recess's depth. Practice 5: six bowls of about 50 mm, each with
a 6.35 mm hole at its bottom 35 mm below the back face; no silhouette shows them.

The recess is cut as a cone from the rim, on our surface, down to the hole's circle at the hole's
end. A cone is the least recess that joins the two circles: a bowl with rounded walls holds it, so
the cut stays in the cavity even though the real wall's shape is not known.

What decides that the line is a rim: at least RAYS_FOUND of the rays from the hole's centre meet a
line of the view at one distance (to within ROUND); the render's tone between hole and rim is not
one even tone (MIN_TONE_SPREAD; practice 6's flat top face, a round line about its blind bore, is);
it is not drawn by an edge of our own solid
there; our solid lies over the hole's end by at least MIN_DEPTH_PX. Of the coaxial ends under one
rim, the one nearest the surface is the recess's bottom. The cut is kept only by the same rule as
the other later steps (the verifier's score, or judge.py with HULL_JUDGE=local) and the volume guard.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad import holes as hl
from agentic_cad import judge, regions, spans, trace, verify
from agentic_cad import visual_hull as vh

ENABLED = os.environ.get("HULL_BOWLS", "1") != "0"
TIMEOUT = float(os.environ.get("HULL_BOWLS_TIMEOUT", 30))
FACING = 0.99         # only the straight view looking along the hole's axis
RAYS = 72
RAYS_FOUND = 0.85     # share of the rays that must meet a line before leaving the part
ROUND = 0.06          # spread of the distances, as a share of their median
START = 1.5           # the rays start this many hole radii out (past the hole's own edge) ...
START_PX = 3          # ... plus this many pixels
MAX_RIM = 6.0         # the rays go out this many hole radii ...
MAX_RIM_SHARE = 0.1   # ... or this share of the view's width, whichever is more
EXPLAINED = 0.5       # the rim may lie on edges our own solid draws for at most this share
MIN_DEPTH_PX = 4      # our solid lies over the hole's end by at least this
MIN_TONE_SPREAD = 8.0 # the render's tones between hole and rim vary this much: a recess, not one flat face
SCORE_SLACK = 0.003   # as in pockets.py


def _rim(view: verify.Observed, centre_px: np.ndarray, hole_px: float) -> float | None:
    """Radius in pixels of a round line about `centre_px`, or None."""
    size = view.mask.shape[0]
    found = []
    for angle in np.linspace(0, 2 * np.pi, RAYS, endpoint=False):
        step = np.array([np.cos(angle), np.sin(angle)])
        for r in np.arange(hole_px * START + START_PX, max(hole_px * MAX_RIM, MAX_RIM_SHARE * size), 0.5):
            x, y = (centre_px + step * r).round().astype(int)
            if not (0 <= x < size and 0 <= y < size) or not view.mask[y, x]:
                break
            if view.lines[y, x]:
                found.append(r)
                break
    if len(found) < RAYS_FOUND * RAYS:
        return None
    median = float(np.median(found))
    return median if float(np.std(found)) <= ROUND * median else None


def _uneven(view: verify.Observed, centre_px: np.ndarray, hole_px: float, rim_px: float) -> float:
    """Standard deviation of the render's tone between the hole and the rim, away from lines."""
    size = view.mask.shape[0]
    yy, xx = np.mgrid[0:size, 0:size]
    distance = np.hypot(xx - centre_px[0], yy - centre_px[1])
    clear = ~(cv2.dilate(view.lines.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
    inside = (distance > hole_px + 3) & (distance < rim_px - 3) & view.mask & clear
    if inside.sum() < 20:
        return 0.0
    return float(regions.tone_of(view.image)[inside].std())


def find(solid: TopoDS_Shape, holes: list[hl.Cylinder], observed: dict, dims: dict[str, float]) -> list[dict]:
    """The recesses to cut: the hole end at their bottom, which way is out, rim and hole radius, depth."""
    pixel = max(dims.values()) * vh.FRAME_MARGIN / verify.SIZE
    intersector = spans._intersector(solid)
    drawings, mesh, found = {}, None, {}
    for hole in holes:
        axis, centre = np.array(hole.axis, float), np.array(hole.centre)
        for sign in (1, -1):
            outward = sign * axis
            end = centre + outward * hole.length / 2
            for name in verify.ORTHO_VIEWS:
                view = observed.get(name)
                if view is None or float(verify.camera(name)[2] @ outward) < FACING:
                    continue
                centre_px = spans._pixels(end[None], view, dims)[0]
                rim_px = _rim(view, centre_px, hole.radius / view.mm_per_px)
                if rim_px is None:
                    continue
                spread = _uneven(view, centre_px, hole.radius / view.mm_per_px, rim_px)
                if spread < MIN_TONE_SPREAD:
                    continue  # one even tone: a flat face square to the view, not a recess
                rim = rim_px * view.mm_per_px
                if name not in drawings:  # a line our own solid draws there is explained already
                    mesh = mesh or verify.tessellate(solid, 0.0005 * max(dims.values()), 0.2)
                    drawing = verify.draw(mesh, name, dims, view.mm_per_px)
                    drawings[name] = cv2.dilate(drawing.sharp.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
                ring = spans._pixels(spans._circle(end, outward, rim, 120), view, dims).round().astype(int)
                ring = ring[(ring >= 0).all(1) & (ring < view.mask.shape[0]).all(1)]
                if len(ring) == 0 or float(drawings[name][ring[:, 1], ring[:, 0]].mean()) > EXPLAINED:
                    continue
                # where our surface is over the recess: the outermost material along the axis, just
                # inside the rim and halfway in, measured from the hole's end
                tops = []
                for point in [*spans._circle(end, outward, 0.8 * rim, 8), *spans._circle(end, outward, 0.5 * rim, 8)]:
                    stretches = spans._material(point, outward, intersector)
                    if stretches is None:
                        continue
                    tops.append(max((b for a, b in stretches if b > 0), default=0.0))
                if len(tops) < 8:
                    continue
                depth = float(np.median(tops))
                if depth < MIN_DEPTH_PX * pixel:
                    continue
                # one recess per rim: the end nearest the surface is its bottom
                key = (name, round(float(centre_px[0])), round(float(centre_px[1])))
                key = next((k for k in found if k[0] == name and abs(k[1] - key[1]) <= 3 and abs(k[2] - key[2]) <= 3), key)
                if key in found and found[key]["depth"] <= depth:
                    continue
                found[key] = {"end": tuple(map(float, end)), "outward": tuple(map(float, outward)),
                              "hole_radius": float(hole.radius), "rim_radius": round(rim, 3),
                              "depth": round(depth, 3), "view": name,
                              "tone_spread": round(spread, 1)}
    return list(found.values())


def _cone(bowl: dict) -> TopoDS_Shape:
    """From the hole's circle at its end out to the rim on our surface, and a little past it."""
    end, outward = np.array(bowl["end"]), np.array(bowl["outward"])
    r1, r2, height = bowl["hole_radius"], bowl["rim_radius"], bowl["depth"]
    over = 1.0  # mm past our surface, so the cut opens it cleanly
    r_top = r2 + (r2 - r1) * over / height
    return BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(*map(float, end)), gp_Dir(*map(float, outward))),
                                r1, r_top, height + over).Shape()


def cut(shape: TopoDS_Shape, bowls: list[dict]) -> TopoDS_Shape | None:
    """`shape` without the cones of `bowls`; one valid solid or None. Runs in a child process."""
    tools = List_TopoDS_Shape()
    for bowl in bowls:
        tools.Append(_cone(bowl))
    arguments = List_TopoDS_Shape()
    arguments.Append(shape)
    operation = BRepAlgoAPI_Cut()
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.Build()
    if not operation.IsDone():
        return None
    result = vh.largest_solid(operation.Shape())
    if result is None or result.IsNull() or not BRepCheck_Analyzer(result).IsValid():
        return None
    return result


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step."""
    if not ENABLED or case_dir is None:
        return solid
    with trace.step("cut_bowls", kind="code",
                    description="Cuts the recess a view draws round a listed hole's mouth under our surface") as step:
        holes = hl.read_holes(case_dir)
        if not holes:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        observed = verify.observe(case_dir, dims)
        bowls = find(solid, holes, observed, dims)
        step.metric(bowls=len(bowls))
        if not bowls:
            step.log("no round line about a listed hole's end lies over our surface")
            return solid
        step.attach("bowls.json", bowls)
        target = vh.read_volume(case_dir)
        rater = judge.Judge(case_dir, dims, observed) if judge.local() else None
        before = None if rater is not None else verify.check(solid, case_dir, observed).score

        def accepted(base, carved) -> bool:
            if rater is not None:
                verdict = rater.rate(base, carved)
                judge.note(agent, "bowls", base, carved, verdict.accepted, verdict)
                return verdict.accepted
            after = verify.check(carved, case_dir, observed).score
            judge.note(agent, "bowls", base, carved, after >= before - SCORE_SLACK)
            step.log(f"the verifier's score {before:.4f} -> {after:.4f}")
            return after >= before - SCORE_SLACK

        carved = agent._guarded(step, solid, target, lambda: agent.cutter.run(cut, solid, bowls, TIMEOUT))
        if carved is None or not accepted(solid, carved):
            step.log("all recesses at once were not kept; trying them one view at a time", level="warning")
            carved, kept = solid, 0
            for name in sorted({b["view"] for b in bowls}):
                group = [b for b in bowls if b["view"] == name]
                attempt = agent._guarded(step, carved, target,
                                         lambda: agent.cutter.run(cut, carved, group, TIMEOUT), quiet=True)
                if attempt is not None and accepted(carved, attempt):
                    carved, kept = attempt, kept + len(group)
                    if rater is None:
                        before = verify.check(carved, case_dir, observed).score
            step.metric(bowls_cut=kept)
            if kept == 0:
                return solid
        else:
            step.metric(bowls_cut=len(bowls))
        agent._record(step, carved, "bowls")
        return carved
