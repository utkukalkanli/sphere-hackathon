"""Deterministic: recesses our solid lacks, found where a view draws an edge our solid does not have.

A straight view draws a line between two faces that both look at its camera only where they lie
at different depths: one is a step down from the other. Where our solid is one level across such
a border it lacks that step, and since it contains the part, one of the two faces lies deeper
than our solid has it (measured on the 24 eval parts: 40 of 46 such borders are steps, on
average 100 px deep).

Which of the two is the deeper one the drawing says where an edge ends against the border in a T
(drawing.occlusions): the face on the far side of the bar hides the other two, so it is the
higher one and the face beside it the deeper (12 of 13 right). Those come first. Where no T
says it, both faces are searched from the same solid and the one the views fit better with is
kept.

How deep is not known. The column over the deeper face is cut at a row of depths and the local
judge keeps the one at which the eight views fit best, if they fit strictly better than before:
the search the model step uses for a recess (propose.try_cuts), with the model's guess of where
replaced by this reading of the views.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import drawing, propose, trace, verify
from agentic_cad import visual_hull as vh

MIN_BORDER_PX = 15     # shorter borders are not read
LEVEL_PX = 2.0         # our solid is one level across a border when its two sides differ by less than this
SIDE_PX = (2.5, 5.0)   # the solid's depth is read this far to each side of a border and carried on to it
FACING = 0.9995        # cosine: our solid's surface looks straight at the camera (as in depth.py)
MIN_SQUARE = 0.9       # share of our solid under a face that must do so
MIN_SIDE = 3           # ... at this many places at least
MIN_AREA_PX = 250      # a deeper face smaller than this is not worth a search
MAX_RECESSES = 6       # per case, the largest first
EITHER = os.environ.get("HULL_RECESS_EITHER", "1") != "0"   # also borders without a T: both sides are tried
MIN_EXCESS = 1.02      # nothing is looked for in a solid within this of the part's volume


def beside(border: drawing.Border, labels: np.ndarray, depth: np.ndarray, mask: np.ndarray) -> dict[int, float] | None:
    """Our solid's depth on the two sides of a border, carried on to the border itself."""
    points = border.points()
    step = np.diff(points, axis=0)
    length = np.linalg.norm(step, axis=1)
    keep = length > 0
    if not keep.any():
        return None
    middle = (points[:-1] + points[1:])[keep] / 2
    across = np.stack([-step[keep, 1], step[keep, 0]], axis=1) / length[keep][:, None]
    size = labels.shape[0]
    found: dict[int, list[float]] = {face: [] for face in border.faces}
    for side in (1, -1):
        near = np.rint(middle + side * SIDE_PX[0] * across).astype(int)
        far = np.rint(middle + side * SIDE_PX[1] * across).astype(int)
        ok = ((near >= 0) & (near < size)).all(axis=1) & ((far >= 0) & (far < size)).all(axis=1)
        near, far = near[ok], far[ok]
        for face in border.faces:
            here = (labels[near[:, 1], near[:, 0]] == face) & (labels[far[:, 1], far[:, 0]] == face) \
                & mask[near[:, 1], near[:, 0]] & mask[far[:, 1], far[:, 0]]
            if face != 0 and here.any():
                found[face] += (2 * depth[near[here, 1], near[here, 0]] - depth[far[here, 1], far[here, 0]]).tolist()
    if any(face == 0 or len(values) < MIN_SIDE for face, values in found.items()):
        return None
    return {face: float(np.median(values)) for face, values in found.items()}


def find(evidence, solid: TopoDS_Shape) -> list[dict]:
    """The faces of the straight views that lie deeper than our solid has them, as recesses to
    search: one face where a T says which side of the border is the deeper, else both sides of
    it as a choice of two ("either")."""
    areas = evidence.areas()
    drawn = evidence.drawings(solid)
    marked: dict[tuple[str, int], dict] = {}             # (view, face) -> recess
    open_pairs: dict[tuple[str, int, int], float] = {}   # (view, face, face) -> length of border without a T
    masks: dict[str, tuple] = {}
    for view in verify.ORTHO_VIEWS:
        if view not in evidence.observed:
            continue
        seen, sketch, ours = evidence.observed[view], drawing.read(evidence.observed[view]), drawn[view]
        facing = {item.label: item for item in areas[view][1] if item.kind == "facing"}
        masks[view] = (sketch.labels, facing)
        towards = verify.camera(view)[2]
        square = (ours.normal @ towards) >= FACING
        flat: dict[int, bool] = {}

        def flat_under(face: int) -> bool:
            """Whether our solid is flat and square to the view under a face: the step the view
            draws is missing from a flat face of ours, not from a curved one that only looks flat
            in one strip (eval 3, a tyre: the strip of its tread that faces the camera)."""
            if face not in flat:
                under = (sketch.labels == face) & ours.mask
                flat[face] = bool(under.sum() >= MIN_AREA_PX // 2 and square[under].mean() >= MIN_SQUARE)
            return flat[face]

        for border in sketch.borders:
            a, b = border.faces
            if a not in facing or b not in facing or border.length < MIN_BORDER_PX:
                continue
            if min(facing[a].area, facing[b].area) < MIN_AREA_PX:
                continue
            sides = beside(border, sketch.labels, ours.depth, ours.mask)
            if sides is None or abs(sides[a] - sides[b]) > LEVEL_PX * seen.mm_per_px:
                continue   # our solid has a step here already
            if not flat_under(a) or not flat_under(b):
                continue   # ... or is not flat under the two faces
            if border.front is None:
                key = (view, min(a, b), max(a, b))
                open_pairs[key] = open_pairs.get(key, 0.0) + border.length
                continue
            deeper = b if border.front == a else a
            entry = marked.setdefault((view, deeper), {"type": "recess", "view": view, "area": sketch.labels == deeper,
                                                       "pixels": facing[deeper].area, "border_px": 0.0})
            entry["border_px"] += border.length
    listed = list(marked.values())
    if EITHER:
        for (view, a, b), length in open_pairs.items():
            if (view, a) in marked or (view, b) in marked:
                continue   # the T on another border of the face has said it already
            labels, facing = masks[view]
            choices = [{"type": "recess", "view": view, "area": labels == face, "pixels": facing[face].area, "border_px": length}
                       for face in (a, b)]
            # the views are judged over the smaller face too, so its size orders the pairs
            listed.append({"type": "either", "view": view, "choices": choices, "border_px": length,
                           "pixels": min(choice["pixels"] for choice in choices)})
    listed = sorted(listed, key=lambda entry: (entry["type"] != "recess", -entry["pixels"]))[:MAX_RECESSES]
    for number, entry in enumerate(listed, 1):
        entry["id"] = f"{entry['type']}_{entry['view']}_{number}"
        for letter, choice in zip("ab", entry.get("choices", [])):
            choice["id"] = f"{entry['id']}{letter}"
    return listed


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step: search the depth of every face the drawing says lies under our solid."""
    with trace.step("open_recesses", kind="code") as step:
        if case_dir is None:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        if target and vh.volume(solid) < MIN_EXCESS * target:
            step.log("the solid already has the part's volume; nothing to look for")
            return solid
        evidence = agent.evidence(case_dir, dims)
        pending = find(evidence, solid)
        step.metric(recesses=len(pending))
        if not pending:
            step.log("no view draws a step, with its deeper side marked, where our solid is level")
            return solid
        step.log("; ".join(f"{entry['view']}: {'one of two faces, the smaller ' if entry['type'] == 'either' else ''}{entry['pixels']} px along "
                           f"{entry['border_px']:.0f} px of border" for entry in pending))
        return propose.try_cuts(agent, step, solid, case_dir, dims, target, evidence, pending, label="recesses")
