"""Open cavities the silhouettes cannot show: a pocket under a rim, open towards one straight view.

Where our solid is flat and faces the camera of a straight view, but the render shows other tones
there (walls, curved floors) inside an area enclosed by the part, the part is lower there than we
are: an opening. Practice 7: the whole interior of the top flange's rim, about 113 x 40 mm, where
our solid was still flat at the top face (about 200 cm3 of its 330 cm3 excess).

How deep: the opening's outline is cut as a pocket, its depth found by halving. A pocket that goes too deep
breaks through a wall somewhere, and some view then shows background where the render shows the
part (views.ViewSet.missing): that only gets worse as the pocket deepens. The deepest pocket that leaves every
silhouette as it was is taken, and kept only if the isometric views' lines do not get worse and
the part's volume is left. Practice 7: the silhouettes hold to 37 mm and drop from 40 mm on.

What this does not see: a floor above the deepest safe depth that no silhouette reaches (the pocket
is then too deep there); the isometric lines are the only guard against that.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import depth as levels
from agentic_cad import regions, trace, verify
from agentic_cad import visual_hull as vh
from agentic_cad.views import ViewSet

ISO = ("iso_front_right_top", "iso_back_left_bottom")
MIN_AREA_SHARE = 0.01     # an opening covers at least this share of the view's part
MIN_AREA_PX = 3000
OUTSIDE_SHARE = 0.01      # an opening may touch the background along at most this share of its pixels
LEVEL_SPREAD_PX = 1.5     # our surface over the opening is one level to within this
MIN_TONE_SPREAD = 8.0     # the render's tones inside vary this much (one even tone is one slanted face)
PRECISION = 0.03          # the depth is found to this share of the box along the view ...
PRECISION_MM = 2.0        # ... or this many mm, whichever is more (about 5 tries)
LINE_SLACK = 0.0          # the isometric views' lines x silhouette may not drop
MAX_OPENINGS = 3          # per case, the largest first
TIMEOUT = float(os.environ.get("HULL_CAVITY_TIMEOUT", 30))
BUDGET = float(os.environ.get("HULL_CAVITY_BUDGET", 120))   # seconds per case for the whole step
BUDGET_SHARE = 0.4        # ... and at most this share of the case's time left


def openings(view: verify.Observed, drawing: verify.Drawing, facing: float) -> list[tuple[np.ndarray, float]]:
    """(area, level) of every opening of one straight view, largest first."""
    _, _, towards = verify.camera(view.name)
    ours_facing = drawing.mask & (drawing.normal @ towards >= levels.FACING)
    tone = regions.tone_of(view.image).astype(float)
    readable = view.mask & ~cv2.dilate(view.lines.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    wrong = ours_facing & readable & (np.abs(tone - facing) > 2 * regions.FACING_TOLERANCE)
    closed = cv2.morphologyEx(wrong.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    outside = cv2.dilate((~view.mask).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    least = max(MIN_AREA_PX, MIN_AREA_SHARE * view.mask.sum())
    tolerance = LEVEL_SPREAD_PX * view.mm_per_px
    found = []
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < least:
            continue
        area = labels == label
        if (area & outside).sum() > OUTSIDE_SHARE * area.sum():
            continue   # open to the background: the silhouettes already had their say there
        ours = drawing.depth[area & drawing.mask]
        if len(ours) < 0.9 * area.sum() or np.ptp(np.percentile(ours, (5, 95))) > tolerance:
            continue   # our surface over it is not one flat level
        inside = area & readable
        if inside.sum() < 0.3 * area.sum() or np.percentile(tone[inside], 92) - np.percentile(tone[inside], 8) < MIN_TONE_SPREAD:
            continue   # one even tone: a single slanted face, which the level-faces step handles
        # the opening as one outline: its own holes (specks, a strip that happens to face the camera) filled
        mask = area.astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        mask = np.zeros_like(mask)
        cv2.drawContours(mask, contours, -1, 255, -1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        found.append((mask > 0, float(np.median(ours))))
    return sorted(found, key=lambda item: -item[0].sum())


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    with trace.step("cut_cavities", kind="code",
                    description="Cuts open cavities under a rim, as deep as every silhouette allows") as step:
        if case_dir is None:
            return solid
        started = time.time()
        budget = BUDGET
        if agent.deadline is not None:
            budget = min(budget, BUDGET_SHARE * (agent.deadline - time.time()))
        target = vh.read_volume(case_dir)
        if target and vh.volume(solid) < 1.05 * target:
            step.log("the solid already has about the part's volume; nothing to look for")
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        observed = verify.observe(case_dir, dims)
        straight = [name for name in verify.ORTHO_VIEWS if name in observed]
        if not straight or not any(name in observed for name in ISO):
            step.log("the straight or the isometric views could not be read")
            return solid
        facing = regions.facing_tone({name: regions._measure(observed[name])[1] for name in straight})
        if facing is None:
            step.log("no facing tone")
            return solid
        centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
        reach = 2 * float(np.linalg.norm(centre))
        deflection = 0.3 * min(view.mm_per_px for view in observed.values())
        mesh = verify.tessellate(solid, deflection)
        candidates = []
        for name in straight:
            drawing = verify.draw(mesh, name, dims, observed[name].mm_per_px)
            candidates += [(area, level, name) for area, level in openings(observed[name], drawing, facing)]
        candidates.sort(key=lambda item: -item[0].sum())
        step.metric(openings=len(candidates))
        if not candidates:
            step.log("no opening: wherever our solid faces a view flat, the render shows the facing tone")
            return solid

        current, results = solid, []
        # Whether a pocket breaks through: the pixels each view shows as part and the solid leaves empty
        # (views.ViewSet.missing, 0.5 s for all 8 views against about 10 s for the verifier's drawings)
        view_set = ViewSet(views, tuple(dims[a] for a in "xyz"), current)
        missing = view_set.missing(current)
        iso_before = verify.check(current, case_dir, observed, views=ISO)
        lines_iso = iso_before.mean("lines") * iso_before.mean("silhouette")
        for area, level, name in candidates[:MAX_OPENINGS]:
            view = observed[name]
            right, up, towards = verify.camera(name)
            half = view.mask.shape[0] / 2

            def to_mm(pixels: np.ndarray) -> np.ndarray:
                return np.column_stack([(pixels[:, 0] + 0.5 - half) * view.mm_per_px,
                                        (half - 0.5 - pixels[:, 1]) * view.mm_per_px])

            outlines = vh.trace_outlines(area.astype(np.uint8) * 255, to_mm, view.mm_per_px)
            if not outlines:
                continue
            extent = float(abs(np.array([dims["x"], dims["y"], dims["z"]]) @ towards))
            base = current

            def pocket(depth_mm: float):
                """The solid with the opening cut `depth_mm` deep, or None; and whether it keeps the views."""
                cut = vh.Silhouette(name=f"cavity_{name}", views=[name], mask=area.astype(np.uint8) * 255,
                                    outlines=outlines, origin=centre + (level - depth_mm) * towards, right=right, up=up,
                                    direction=towards, depth=reach, info={"depth": round(depth_mm, 2)})
                carved = agent._guarded(step, base, target, lambda: agent.cutter.run(levels.remove, base, [cut], TIMEOUT),
                                        quiet=True, share=1.0)
                if carved is None:
                    return None, False
                after = view_set.missing(carved)
                return carved, all(after[v] - missing.get(v, 0) <= view_set.tolerance(v) for v in after)

            # The silhouettes only get worse as the pocket deepens, so the deepest depth that keeps them
            # is found by halving the interval between a depth that keeps them and one that does not.
            good, good_depth, bad_depth, timed_out = None, 0.0, extent, False
            while bad_depth - good_depth > max(PRECISION_MM, PRECISION * extent):
                if time.time() - started > budget or agent.out_of_time():
                    timed_out = True
                    break
                depth_mm = (good_depth + bad_depth) / 2
                carved, keeps = pocket(float(depth_mm))
                if keeps:
                    good, good_depth = carved, float(depth_mm)
                else:
                    bad_depth = float(depth_mm)
            record = {"view": name, "pixels": int(area.sum()), "depth_mm": round(good_depth, 2),
                      "first_bad_mm": round(bad_depth, 2)}
            if good is None:
                results.append({**record, "result": "out of time" if timed_out else "no depth keeps every silhouette"})
                continue
            iso_after = verify.check(good, case_dir, observed, views=ISO)
            lines_after = iso_after.mean("lines") * iso_after.mean("silhouette")
            if lines_after < lines_iso - LINE_SLACK:
                results.append({**record, "result": "isometric lines worse", "iso": [round(lines_iso, 4), round(lines_after, 4)]})
                continue
            removed = vh.volume(current) - vh.volume(good)
            step.log(f"{name}: an opening of {int(area.sum())} px cut {good_depth:.1f} mm deep, {removed / 1000:.1f} cm3")
            results.append({**record, "result": "kept", "removed_mm3": round(removed, 1),
                            "iso": [round(lines_iso, 4), round(lines_after, 4)]})
            current, lines_iso = good, lines_after
            missing = view_set.missing(current)
        step.attach("cavities.json", results)
        step.metric(kept=sum(r["result"] == "kept" for r in results), seconds=round(time.time() - started, 1))
        if current is solid:
            return solid
        agent._record(step, current, "cavities")
        return current
