"""Step, deterministic: an area left standing inside a lowered floor needs the isometric view's word.

The depth steps lower the areas between a view's lines one by one. An area for which they find no
depth stays where it was, and if everything around it went down it now stands up as an island: a
boss the part may not have. (Eval 110: a tray whose floor was lowered, with six engraved letters
left standing 3.35 mm above it; in the part they are sunk into the floor. Eval 182: five such.)

A real island shows in the isometric view: its top outline is drawn there (14 real ones on the 24
eval parts: 0.62 to 1.00 of the outline on lines, 13 of them 0.75 or more). An island the part does
not have is drawn at no such place (15 false ones: 0.09 to 0.60, 14 of them 0.45 or less). Below
DRAWN the island is lowered to the highest floor around it: flush, the least that is certain. Its
own depth below that, if it has one, stays for the pocket and shading steps.

Kept only if no view's outline loses area, the part's volume is left and the number of
through-holes does not pass the stated one (`_guarded`).
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import depth as levels
from agentic_cad import hole_count
from agentic_cad import lines, pockets, trace, verify
from agentic_cad import visual_hull as vh
from agentic_cad.views import ViewSet

ENABLED = os.environ.get("HULL_ISLANDS", "1") != "0"
TIMEOUT = float(os.environ.get("HULL_ISLANDS_TIMEOUT", 30))
BUDGET = float(os.environ.get("HULL_ISLANDS_BUDGET", 60))   # seconds per case
MIN_AREA_PX = 150
RING_PX = 4          # the floor around an island is read this far outside it
STEP_PX = 2.0        # the island stands at least this far above everything around it
DRAWN = 0.5          # share of the top outline on isometric lines from which an island is real
SEEN = 0.9           # share of the outline that must fall inside the isometric view to be judged
NEAR_PX = 2.5
MAX_ISLANDS = 12     # per view, the largest first


def find(view: str, drawing: verify.Drawing, observed: dict, dims: dict) -> list[pockets.Pocket]:
    """The islands of one straight view that the isometric view does not draw, as cuts down to the
    floor around them, the largest first."""
    here, iso = observed[view], observed.get(lines.iso_for(view))
    if iso is None:
        return []
    towards = verify.camera(view)[2]
    step = here.mm_per_px
    level = np.where(drawing.mask, np.round(drawing.depth / step), -1e9)
    flat = drawing.mask & (np.abs(drawing.normal @ towards) > 0.999)
    distance = cv2.distanceTransform((~iso.lines).astype(np.uint8), cv2.DIST_L2, 3)
    kernel = np.ones((2 * RING_PX + 1, 2 * RING_PX + 1), np.uint8)
    found = []
    for value in np.unique(level[flat]):
        count, labels, stats, _ = cv2.connectedComponentsWithStats((flat & (level == value)).astype(np.uint8), connectivity=4)
        for index in range(1, count):
            if stats[index, cv2.CC_STAT_AREA] < MIN_AREA_PX:
                continue
            region = labels == index
            ring = (cv2.dilate(region.astype(np.uint8), kernel) > 0) & ~region
            if not ring.any() or not drawing.mask[ring].all():
                continue   # it reaches the outline of the part: not enclosed
            top = float(np.median(drawing.depth[region]))
            around = drawing.depth[ring]
            floor = float(np.percentile(around, 90))   # the highest floor around it
            if floor > top - STEP_PX * step:
                continue
            contours, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            outline = max(contours, key=len)[:, 0, :].astype(float)
            start = lines.lift(view, outline, np.full(len(outline), top), dims, step, here.mask.shape[0])
            landed, _ = lines.land(iso.name, start, iso.mm_per_px, iso.mask.shape[0])
            near, inside = lines._at(distance, landed)
            if inside.mean() < SEEN:
                continue
            drawn = float(((near <= NEAR_PX) & inside).mean())
            if drawn >= DRAWN:
                continue   # the isometric view draws it: a boss of the part
            found.append(pockets.Pocket(view, region, top, top - floor, drawn, 0.0))
    found.sort(key=lambda island: -int(island.region.sum()))
    return found[:MAX_ISLANDS]


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step."""
    import time
    with trace.step("lower_islands", kind="code", description=(
            "Lowers areas left standing inside a lowered floor that the isometric view does not draw")) as step:
        if not ENABLED or case_dir is None:
            return solid
        started = time.time()
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        observed = agent.evidence(case_dir, dims).observed
        # A solid with more through-holes than the part is wrong in ways this step cannot judge: what
        # stands up in it need not be an island left by a depth step (eval 99: 193 holes for 33, and
        # lowering its "islands" took the overlap from 0.37 to 0.20).
        stated = hole_count.stated(case_dir)
        holes = hole_count.genus(vh.tidy(solid))
        if stated is not None and holes > stated:
            step.log(f"the solid has {holes} through-holes for the part's {stated}; nothing is lowered")
            return solid
        current, proposed, kept, view_set, missing = solid, 0, 0, None, None
        for view in verify.ORTHO_VIEWS:
            if view not in observed or agent.out_of_time() or time.time() - started > BUDGET:
                continue
            mesh = verify.tessellate(current, max(dims.values()) / 2000)
            drawing = verify.draw(mesh, view, dims, observed[view].mm_per_px)
            for island in find(view, drawing, observed, dims):
                if agent.out_of_time() or time.time() - started > BUDGET:
                    break
                proposed += 1
                cut = pockets.cut_for(island, observed[view], dims)
                if cut is None:
                    continue
                base = current
                carved = agent._guarded(step, base, target, lambda: agent.cutter.run(levels.remove, base, [cut], TIMEOUT),
                                        quiet=True)
                if carved is None or vh.volume(carved) >= vh.volume(base) * (1 - 1e-6):
                    continue
                if view_set is None:
                    view_set = ViewSet(views, tuple(dims[a] for a in "xyz"), solid)
                    missing = view_set.missing(solid)
                after = view_set.missing(carved)
                if any(after[name] - missing.get(name, 0) > view_set.tolerance(name) for name in after):
                    continue   # the island was part of some view's outline
                if stated is not None and hole_count.genus(vh.tidy(carved)) > max(stated, holes):
                    continue   # lowering it opened a hole the part does not have
                step.log(f"{view}: an area of {int(island.region.sum())} px standing {island.drop:.2f} mm above the floor "
                         f"around it, {island.on_line:.2f} of its outline drawn in the isometric view: lowered to that floor")
                current, kept = carved, kept + 1
        step.metric(islands=proposed, lowered=kept, seconds=round(time.time() - started, 1))
        if current is solid:
            return solid
        agent.lines_found = None
        agent._guarded(step, solid, target, lambda: current, quiet=True)   # records the volume and what was removed
        agent._record(step, current, "islands")
        return current
