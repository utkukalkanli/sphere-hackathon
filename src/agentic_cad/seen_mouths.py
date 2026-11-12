"""Step, deterministic: a mouth a view draws over a listed hole is cut down to that hole.

facts.json lists the full cylindrical holes only. A conical or free-form opening (a port, a tapered
counterbore, a valve seat) is in no list, and no silhouette shows it. But a straight view that looks
into it draws its mouth: a circle on a flat face, where our solid has no edge. Where such a circle
stands over a listed hole that lies buried under that face, on the same axis and thinner, the
opening ends where that hole begins: facts.json gives the depth, the view the diameter. (Practice 5:
six ports of 50 mm, 35 mm deep, each over a 6.35 mm hole; about half of that part's excess.)

Two rings around one centre (a step or a chamfer at the mouth) are one mouth; the inner ring is cut.
The view must show a wall just inside the circle (another tone than the face around it), and the
opening must be at least 0.3 diameters deep: without these the rule also took bosses, the inner edge
of grooves and chamfers on the eval set (48 parts: 10 cuts, most of them into the part).
A cut is kept only if it leaves the part's volume and the view then finds an edge of ours under at
least half of the circle that it missed before.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import holes as hl
from agentic_cad import trace
from agentic_cad import verify
from agentic_cad import visual_hull as vh

ENABLED = os.environ.get("HULL_SEEN_MOUTHS", "1") != "0"
TIMEOUT = float(os.environ.get("HULL_SEEN_MOUTHS_TIMEOUT", 30))
MIN_RADIUS_PX = 6
FIT_PX = 1.5        # a component is a circle if its pixels lie this close to the fitted one (rms)
COVERAGE = 0.7      # ... and cover this share of its circumference
BAND_PX = 8         # two rings of one mouth lie this close together
COAXIAL_PX = 2.0    # the listed hole's axis passes this close to the circle's centre
BURIED_PX = 2.0
SAME_PX = 1.5       # a circle this close in radius to a listed coaxial hole is that hole
MIN_DEPTH = 0.3     # the opening is at least this deep, in diameters
WALL_TONE = 12      # grey levels by which the band inside the circle differs from the face around it
NESTED = (0.3, 0.9)  # another circle about the same centre with this share of the radius: a ring, not a mouth
RING_PX = 3         # the cut's own ring: its line lies this close to the cut's radius
EXPLAINS = 0.5      # share of the circle's missed line that the cut must put an edge under


def fit_circle(points: np.ndarray):
    """Least-squares circle through pixel coordinates (n, 2): centre, radius, rms distance, coverage."""
    x, y = points[:, 0].astype(float), points[:, 1].astype(float)
    a = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    solution, *_ = np.linalg.lstsq(a, x * x + y * y, rcond=None)
    cx, cy = solution[0], solution[1]
    radius = float(np.sqrt(max(solution[2] + cx * cx + cy * cy, 0.0)))
    distance = np.hypot(x - cx, y - cy)
    rms = float(np.sqrt(np.mean((distance - radius) ** 2)))
    angles = np.arctan2(y - cy, x - cx)
    bins = np.unique(np.floor((angles + np.pi) / (2 * np.pi) * 72).astype(int))
    return np.array([cx, cy]), radius, rms, len(bins) / 72


def circles(observed, drawing) -> list[tuple[np.ndarray, float, float]]:
    """Circles among the lines of the view that the solid's own drawing has no edge for: centre,
    radius, and the radius of the outermost ring of the same mouth (the same, for a single ring)."""
    near = cv2.dilate((drawing.sharp | drawing.edges).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    missing = (observed.lines & ~near).astype(np.uint8)
    count, labels = cv2.connectedComponents(cv2.dilate(missing, np.ones((3, 3), np.uint8)), connectivity=8)
    found = []
    for label in range(1, count):
        ys, xs = np.nonzero((labels == label) & (missing > 0))
        if len(xs) < 40:
            continue
        centre, radius, rms, coverage = fit_circle(np.column_stack([xs, ys]))
        if radius < MIN_RADIUS_PX or coverage < COVERAGE:
            continue
        if rms > FIT_PX:
            # two rings around one centre (a mouth with a step or a chamfer): all pixels in a narrow
            # band. The inner ring is the opening that goes down.
            distance = np.hypot(xs - centre[0], ys - centre[1])
            low, high = np.percentile(distance, [5, 95])
            if high - low > max(BAND_PX, 0.2 * radius):
                continue
            found.append((centre, float(np.percentile(distance, 15)), float(high)))
            continue
        found.append((centre, radius, radius))
    return found


def walls_inside(image: np.ndarray, centre_px: np.ndarray, radius_px: float, outer_px: float) -> bool:
    """Whether the view shows a wall going down just inside the circle: an opening that is in no list
    is not a cylinder, so a view along its axis sees its wall, in another tone than the flat face
    around the circle. A boss, or the inner edge of a groove, has the face's tone inside."""
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(float)
    ys, xs = np.indices(grey.shape)
    distance = np.hypot(xs - centre_px[0], ys - centre_px[1])
    inside = grey[(distance >= radius_px - 9) & (distance <= radius_px - 4)]
    outside = grey[(distance >= outer_px + 4) & (distance <= outer_px + 9)]
    if len(inside) < 30 or len(outside) < 30:
        return False
    low, high = np.percentile(inside, [10, 90])
    # a wall in shadow has another tone than the face; a lit one runs through many tones
    return abs(float(np.median(inside)) - float(np.median(outside))) >= WALL_TONE or high - low >= 2 * WALL_TONE


def candidates(solid, case_dir: Path, observed: dict | None = None) -> list[dict]:
    """The cuts: for every circle a straight view draws on a flat face of ours, without an edge of ours
    under it, that stands over a listed hole buried under that face, a cylinder of the circle's
    diameter from the face down to the near end of that hole."""
    box = json.loads((case_dir / "facts.json").read_text())["bounding_box_mm"]
    dims = {k: float(box[k]) for k in "xyz"}
    middle = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    if observed is None:
        observed = verify.observe(case_dir, dims)
    mesh = verify.tessellate(solid, max(dims.values()) / 2000)
    holes = hl.read_holes(case_dir)
    result = []
    for view in verify.ORTHO_VIEWS:
        if view not in observed:
            continue
        seen = observed[view]
        drawing = verify.draw(mesh, view, dims, seen.mm_per_px)
        right, up, towards = verify.camera(view)
        half = drawing.mask.shape[0] / 2
        drawn = circles(seen, drawing)
        for centre_px, radius_px, outer_px in drawn:
            if any(np.hypot(*(other - centre_px)) < 0.2 * radius_px and NESTED[0] * radius_px < size < NESTED[1] * radius_px
                   for other, size, _ in drawn):
                continue   # rings inside one another: a groove around a boss, which a cylinder would take with it
            radius = radius_px * seen.mm_per_px
            u = (centre_px[0] - half + 0.5) * seen.mm_per_px
            v = (half - 0.5 - centre_px[1]) * seen.mm_per_px
            # our surface around the circle: the depth just outside it
            ring = np.linspace(0, 2 * np.pi, 72, endpoint=False)
            xs = np.clip(np.round(centre_px[0] + (outer_px + 3) * np.cos(ring)).astype(int), 0, drawing.mask.shape[1] - 1)
            ys = np.clip(np.round(centre_px[1] + (outer_px + 3) * np.sin(ring)).astype(int), 0, drawing.mask.shape[0] - 1)
            depths = drawing.depth[ys, xs][drawing.mask[ys, xs]]
            if len(depths) < 36 or np.ptp(depths) > 2 * seen.mm_per_px:
                continue   # not one flat face around it
            surface = float(np.median(depths))
            best, listed = None, False
            for hole in holes:
                axis = np.array(hole.axis)
                offset = np.array(hole.centre) - middle
                if abs(axis @ towards) < 0.999 or \
                        np.hypot(offset @ right - u, offset @ up - v) > COAXIAL_PX * seen.mm_per_px + 0.02 * radius:
                    continue
                if abs(hole.radius - radius) <= SAME_PX * seen.mm_per_px:
                    listed = True   # the circle of a listed hole: the hole and mouth steps deal with it
                if hole.radius >= radius - seen.mm_per_px:
                    continue
                end = float(offset @ towards + hole.length / 2)   # the end that faces the camera
                if end < surface - BURIED_PX * seen.mm_per_px and (best is None or end > best[0]):
                    best = (end, hole)
            if best is None or listed:
                continue
            end, hole = best
            if surface - end < MIN_DEPTH * 2 * radius:
                continue   # a chamfer or a countersink: a cylinder is the wrong shape for it
            if not walls_inside(seen.image, centre_px, radius_px, outer_px):
                continue   # a circle on a flat face with nothing going down inside it: a boss, a groove's inner edge
            start = middle + right * u + up * v + towards * end
            result.append({"view": view, "radius": radius, "depth": surface - end, "start": start, "axis": towards,
                           "length": surface - end + 0.05, "over_hole_d": 2 * hole.radius,
                           "centre_px": centre_px, "radius_px": radius_px})
    # the same mouth seen as two rings (a step or a chamfer): keep the larger for each axis
    kept = []
    for cut in sorted(result, key=lambda c: -c["radius"]):
        if not any(k["view"] == cut["view"] and np.linalg.norm(np.cross(cut["start"] - k["start"], k["axis"])) < 0.5 * k["radius"]
                   for k in kept):
            kept.append(cut)
    return kept



def missed(solid: TopoDS_Shape, cut: dict, seen, dims: dict[str, float]) -> int:
    """Pixels of the view's lines on the cut's circle that no edge of `solid` lies under."""
    mesh = verify.tessellate(solid, max(dims.values()) / 2000)
    drawing = verify.draw(mesh, cut["view"], dims, seen.mm_per_px)
    near = cv2.dilate((drawing.sharp | drawing.edges).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    ys, xs = np.nonzero(seen.lines & ~near)
    distance = np.hypot(xs - cut["centre_px"][0], ys - cut["centre_px"][1])
    return int((np.abs(distance - cut["radius_px"]) <= RING_PX).sum())


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step."""
    with trace.step("seen_mouths", kind="code", description=(
            "Cuts the openings a view draws over a listed hole, down to that hole")) as step:
        if not ENABLED or case_dir is None:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        observed = agent.evidence(case_dir, dims).observed
        found = candidates(solid, case_dir, observed)
        step.metric(mouths=len(found))
        if not found:
            return solid
        current, kept = solid, 0
        for cut in found:
            if agent.out_of_time():
                break
            cylinder = hl.Cylinder(tuple(map(float, cut["start"] + cut["axis"] * cut["length"] / 2)),
                                   tuple(map(float, cut["axis"])), float(cut["radius"]), float(cut["length"]))
            carved = agent._guarded(step, current, target, lambda: agent.cutter.run(
                hl.cut_holes, current, [cylinder], TIMEOUT), quiet=True)
            if carved is None:
                continue
            seen = observed[cut["view"]]
            before, after = missed(current, cut, seen, dims), missed(carved, cut, seen, dims)
            if after > (1 - EXPLAINS) * before:
                step.log(f"{cut['view']}: the cut of diameter {2 * cut['radius']:.1f} explains too little of its "
                         f"circle ({before} -> {after} px); not kept", level="warning")
                continue
            step.log(f"{cut['view']}: an opening of diameter {2 * cut['radius']:.1f} mm cut {cut['depth']:.1f} mm deep, "
                     f"down to the listed hole of diameter {cut['over_hole_d']:.2f}")
            current, kept = carved, kept + 1
        step.metric(kept=kept)
        if current is solid:
            return solid
        agent._record(step, current, "seen_mouths")
        return current
