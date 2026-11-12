"""Step after the shading, deterministic: pockets whose outline a straight view draws under our surface.

lines.py lists the lines of a straight view that run through the inside of one face of ours and says
which of them lie under that face ("below": laid on our surface they land on no line of the
isometric view). Where such lines close into a figure, the figure is the outline of something the
part has further down: the floor of a pocket the silhouettes could not show. A figure that is open
but ends on other lines at both ends counts too: a pocket that shares a side with its neighbour.

The figure says where; the isometric view says how deep. The outline is slid down along the view
direction and looked up in the isometric view at every depth. At the depth of the floor it lands on
the lines the isometric view draws around that floor, as far as the walls of the pocket let the
camera see them. That depth is taken when at least half of the outline lands on lines there and
clearly more than at other depths. Only the depth is searched: the outline is fixed by the straight
view (knowledge.md: a face placed by its outline alone with three free numbers lands on lines
somewhere; with one it did not).

What is cut is the column over the figure down to that depth. As in the other later steps a cut is
kept only if the verifier does not rate the solid lower and at least the part's volume is left.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import depth as levels
from agentic_cad import lines, shading, split, trace, verify
from agentic_cad import visual_hull as vh

UNDER = ("below", "unseen")   # the pieces a figure under our surface may be made of ...
MIN_BELOW = 0.5        # ... and the share of its length that must be "below"
MIN_OPENING_PX = 6     # an open figure whose two ends lie closer than this is not closed by a shared side
OWN_LINE_PX = 3.5      # line pixels further than this from a figure's own points belong to another line
MIN_AREA_PX = 150      # smaller figures are not worth a cut
EDGE_PX = 3            # the surface over a figure is read this far inside its outline
LEVEL_PX = 1.5         # the surface over a figure is flat to within this
MIN_FLAT = 0.8         # share of the figure over which our surface must be at one level
MIN_DROP_PX = 3.0      # a pocket shallower than this is not cut
NEAR_PX = 2.5          # a point lands on a line of the isometric view when it is this close to one
MIN_ON_LINE = 0.5      # share of the outline that must land on lines at the chosen depth
MIN_PEAK = 0.25        # ... and how much more that is than at a usual depth
PLATEAU = 0.03         # depths this close to the best count as equally good: the shallowest is taken
MAX_DEPTHS = 400
MAX_POCKETS = 12       # per view, the largest first
MAX_SINGLE = 6         # when a view's pockets are refused together, this many are tried one by one
MIN_EXCESS = 1.02      # the step is skipped when the solid has less than this times the true volume
SCORE_SLACK = 0.003    # the verifier's score may drop by this much through a cut and still pass
TIMEOUT = float(os.environ.get("HULL_POCKETS_TIMEOUT", 30))
BUDGET = float(os.environ.get("HULL_POCKETS_BUDGET", 30))   # seconds per case for the whole step


@dataclass
class Pocket:
    view: str
    region: np.ndarray     # bool, the pixels of the straight view inside the figure
    surface: float         # mm towards the camera from the box centre: our surface over it
    drop: float            # mm from there down to the floor
    on_line: float         # share of the outline on isometric lines at that depth
    usual: float           # ... and at a usual depth

    def summary(self) -> dict:
        return {"view": self.view, "area_px": int(self.region.sum()), "drop_mm": round(self.drop, 3),
                "on_line": round(self.on_line, 3), "usual": round(self.usual, 3)}


def closed(figure: list[tuple]) -> bool:
    if figure[0][0].kind == "circle":
        return True
    return len(figure) >= 2 and float(np.hypot(*(figure[0][1] - figure[-1][2]))) < 1e-6


def held(figure: list[tuple], view_lines: np.ndarray) -> bool:
    """Whether an open figure ends on other lines of the view at both ends: a pocket that shares a
    side with its neighbour, or one that runs out to a rim on our surface (eval 236), is drawn as
    sides that end on that other line. It is closed straight across between its two ends, which
    under a curved rim leaves a little of the pocket uncut. `view_lines` is the view's line mask:
    the line a figure ends on need not be one of the fitted pieces."""
    if figure[0][0].kind == "circle":
        return False
    own = np.vstack([piece.points for piece, _, _ in figure])
    # the ends as the pieces have them, not carried on: where the lines of the figure really stop
    tips = []
    for (piece, start, end), reference in ((figure[0], figure[0][1]), (figure[-1], figure[-1][2])):
        a, b = piece.points[0], piece.points[-1]
        tips.append(a if np.hypot(*(a - reference)) <= np.hypot(*(b - reference)) else b)
    if np.hypot(*(tips[0] - tips[1])) < MIN_OPENING_PX:
        return False
    reach = int(split.JOIN_PX)
    size = view_lines.shape[0]
    for tip in tips:
        col, row = int(round(tip[0])), int(round(tip[1]))
        rows, cols = np.nonzero(view_lines[max(row - reach, 0):min(row + reach + 1, size),
                                           max(col - reach, 0):min(col + reach + 1, size)])
        if len(rows) == 0:
            return False
        around = np.column_stack([cols + max(col - reach, 0), rows + max(row - reach, 0)]).astype(float)
        # line pixels near the end that are not the figure's own line
        apart = np.hypot(*(around[:, None, :] - own[None, :, :]).transpose(2, 0, 1)).min(axis=1)
        if not (apart > OWN_LINE_PX).any():
            return False
    return True


def polygon(figure: list[tuple]) -> np.ndarray:
    """The figure as a closed run of pixels (column, row)."""
    runs = []
    for piece, start, end in figure:
        points = piece.points
        if start is not None and np.hypot(*(points[0] - start)) > np.hypot(*(points[-1] - start)):
            points = points[::-1]
        runs.append(points if start is None else np.vstack([start, points, end]))
    return np.vstack(runs)


def floor(view: str, outline: np.ndarray, surface: float, observed: dict, dims: dict) -> tuple[float, float, float] | None:
    """How far under our surface the outline lands on the lines of the isometric view: (drop in mm,
    share on lines there, share at a usual depth), or None when no depth stands out."""
    here, iso = observed[view], observed.get(lines.iso_for(view))
    if iso is None:
        return None
    towards = verify.camera(view)[2]
    extent = float(np.abs(towards) @ np.array([dims["x"], dims["y"], dims["z"]]))
    deepest = surface + extent / 2   # down to the far face of the box
    drops = np.arange(MIN_DROP_PX * here.mm_per_px, deepest - here.mm_per_px, here.mm_per_px)
    if len(drops) < 3:
        return None
    if len(drops) > MAX_DEPTHS:
        drops = np.linspace(drops[0], drops[-1], MAX_DEPTHS)
    distance = cv2.distanceTransform((~iso.lines).astype(np.uint8), cv2.DIST_L2, 3)
    start = lines.lift(view, outline, np.full(len(outline), surface), dims, here.mm_per_px, here.mask.shape[0])
    shares = np.empty(len(drops))
    for index, drop in enumerate(drops):
        landed, _ = lines.land(iso.name, start - drop * towards, iso.mm_per_px, iso.mask.shape[0])
        near, inside = lines._at(distance, landed)
        shares[index] = float(((near <= NEAR_PX) & inside).mean())
    best, usual = float(shares.max()), float(np.median(shares))
    if best < MIN_ON_LINE or best - usual < MIN_PEAK:
        return None
    chosen = int(np.argmax(shares >= best - PLATEAU))
    return float(drops[chosen]), float(shares[chosen]), usual


def find(view: str, pieces: list[lines.Piece], observed: dict, drawing: verify.Drawing, dims: dict) -> list[Pocket]:
    """The pockets one straight view shows under our surface, the largest first."""
    here = observed[view]
    size = here.mask.shape[0]
    groups: dict[int, list] = {}
    for piece in pieces:
        if piece.verdict in UNDER:
            groups.setdefault(piece.face, []).append(piece)
    kernel = np.ones((2 * EDGE_PX + 1, 2 * EDGE_PX + 1), np.uint8)
    found = []
    for members in groups.values():
        for figure in split.figures(members):
            length = sum(piece.length for piece, _, _ in figure)
            under = sum(piece.length for piece, _, _ in figure if piece.verdict == "below")
            if under < MIN_BELOW * length or not (closed(figure) or held(figure, here.lines)):
                continue
            if figure[0][0].kind == "circle":
                piece = figure[0][0]
                angles = np.linspace(0, 2 * np.pi, max(24, int(2 * np.pi * piece.radius / 2)), endpoint=False)
                outline = np.array(piece.centre) + piece.radius * np.stack([np.cos(angles), np.sin(angles)], axis=1)
            else:
                outline = polygon(figure)
            region = np.zeros((size, size), np.uint8)
            cv2.fillPoly(region, [np.rint(outline).astype(np.int32)], 1)
            if region.sum() < MIN_AREA_PX:
                continue
            core = (cv2.erode(region, kernel) > 0) & drawing.mask
            if core.sum() < 10:
                continue
            depths = drawing.depth[core]
            surface = float(np.median(depths))
            if (np.abs(depths - surface) <= LEVEL_PX * here.mm_per_px).mean() < MIN_FLAT:
                continue   # our solid has something there already: not one face over one pocket
            result = floor(view, outline, surface, observed, dims)
            if result is not None:
                found.append(Pocket(view, region > 0, surface, *result))
    found.sort(key=lambda pocket: -int(pocket.region.sum()))
    return found[:MAX_POCKETS]


def cut_for(pocket: Pocket, observed: verify.Observed, dims: dict) -> vh.Silhouette | None:
    """The column over a pocket, from its floor towards the camera, as the level-face step cuts them."""
    right, up, towards = verify.camera(pocket.view)
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    half = observed.mask.shape[0] / 2

    def to_mm(pixels: np.ndarray) -> np.ndarray:
        return np.column_stack([(pixels[:, 0] + 0.5 - half) * observed.mm_per_px,
                                (half - 0.5 - pixels[:, 1]) * observed.mm_per_px])

    mask = pocket.region.astype(np.uint8) * 255
    outlines = vh.trace_outlines(mask, to_mm, observed.mm_per_px)
    if not outlines:
        return None
    level = pocket.surface - pocket.drop
    return vh.Silhouette(name=f"pocket_{pocket.view}", views=[pocket.view], mask=mask, outlines=outlines,
                         origin=centre + level * towards, right=right, up=up, direction=towards,
                         depth=2 * float(np.linalg.norm(centre)), info=pocket.summary())


def picture(observed: verify.Observed, pockets: list[Pocket], kept: list[Pocket]) -> bytes:
    """PNG of a view: the pockets that were cut green, the ones proposed and refused red."""
    image = observed.image.copy()
    for pocket in pockets:
        colour = np.array((0, 190, 0) if any(pocket is k for k in kept) else (0, 0, 255))
        image[pocket.region] = (0.5 * image[pocket.region] + 0.5 * colour).astype(np.uint8)
    return cv2.imencode(".png", image)[1].tobytes()


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step."""
    with trace.step("cut_pockets", kind="code",
                    description="Cuts pockets whose outline the views draw under our surface") as step:
        if case_dir is None:
            return solid
        started = time.time()
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        if target and vh.volume(solid) < MIN_EXCESS * target:
            step.log("the solid already has the part's volume; nothing to look for")
            return solid
        observed = verify.observe(case_dir, dims)
        if not any(name in observed for name in lines.ISOMETRIC):
            step.log("no isometric view with a known scale; no depth can be found")
            return solid
        # On the solid with its same-surface faces merged, as the export writes it: then the split
        # step, which comes next and needs the same lines, does not have to find them again.
        solid = vh.tidy(solid)
        drawings = shading.Drawings(solid, observed, dims)
        found = lines.find(observed, drawings, dims)
        if not any(piece.verdict == "below" for pieces in found.values() for piece in pieces):
            step.log("no line of the views lies under our surface")
            agent.lines_found = (solid, observed, drawings, found)
            return solid
        score = before = None   # the verifier's rating, asked for only once there is something to cut
        current, proposed, kept = solid, 0, []

        def attempt(base, pockets, rating):
            cuts = [cut for cut in (cut_for(p, observed[p.view], dims) for p in pockets) if cut is not None]
            if not cuts:
                return None, rating
            carved = agent._guarded(step, base, target, lambda: agent.cutter.run(levels.remove, base, cuts, TIMEOUT), quiet=True)
            if carved is None or vh.volume(carved) >= vh.volume(base) * (1 - 1e-6):
                return None, rating
            after = verify.check(carved, case_dir, observed).score
            if after < rating - SCORE_SLACK:
                step.log(f"the verifier rates the result lower ({rating:.3f} to {after:.3f}); not kept", level="warning")
                return None, rating
            return carved, after

        for view in verify.ORTHO_VIEWS:
            if view not in found or agent.out_of_time() or time.time() - started > BUDGET:
                continue
            pockets = find(view, found[view], observed, drawings[view], dims)
            if not pockets:
                continue
            proposed += len(pockets)
            if score is None:
                score = before = verify.check(solid, case_dir, observed).score
            carved, rating = attempt(current, pockets, score)
            taken = pockets if carved is not None else []
            if carved is None and len(pockets) > 1:
                for pocket in pockets[:MAX_SINGLE]:
                    if time.time() - started > BUDGET:
                        break
                    single, rating = attempt(current if carved is None else carved, [pocket], score)
                    if single is not None:
                        carved, score, taken = single, rating, taken + [pocket]
            if carved is not None:
                current, score = carved, rating
                kept += taken
            step.attach(f"pockets_{view}.png", picture(observed[view], pockets, taken))
        step.metric(pockets_found=proposed, pockets_cut=len(kept),
                    **({"score_before": round(before, 4), "score_after": round(score, 4)} if score is not None else {}))
        step.attach("pockets.json", [p.summary() for p in kept])
        if not kept:
            step.log("no pocket was cut")
            agent.lines_found = (solid, observed, drawings, found)
            return solid
        agent._guarded(step, solid, target, lambda: current, quiet=True)   # records volume and what was removed
        agent._record(step, current, "pockets")
        return current
