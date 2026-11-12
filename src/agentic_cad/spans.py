"""Part of step 2: a listed hole that the views show closed is not one hole.

facts.json gives coaxial cylinder faces of one diameter as one entry: one centre and one length, the
span from the outer end of the first to the outer end of the last. Two blind holes drilled towards
each other from opposite faces are listed like one through hole (practice 8). Cut as listed, the
cylinder opens a line of sight through the part where both views along the axis show material.

Such a hole is replaced here by a blind hole from each end of the span. Nothing in the inputs gives
its depth (the isometric views show the wall and its seam, not the bottom), so it is a guess: half
of the way to where it would break through its wall or meet the hole coming from the other side,
the same for all holes of one facts.json entry.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import numpy as np
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

from agentic_cad import verify
from agentic_cad.holes import Cylinder

ENABLED = os.environ.get("HULL_HOLE_SPANS", "1") != "0"
GUESS_SHARE = 0.5  # depth of a blind hole: this share of the way to the break-through


def _material(point: np.ndarray, axis: np.ndarray, intersector) -> list[tuple[float, float]] | None:
    """The stretches of the line through `point` along `axis` that lie inside the solid, as pairs of
    positions along the axis measured from `point`. None when the crossings do not pair up."""
    intersector.Perform(gp_Lin(gp_Pnt(*map(float, point)), gp_Dir(*map(float, axis))), -1e6, 1e6)
    if not intersector.IsDone():
        return None
    crossings = sorted(intersector.WParameter(i) for i in range(1, intersector.NbPnt() + 1))
    merged = []
    for value in crossings:  # a line through an edge is reported once per face
        if not merged or value - merged[-1] > 1e-4:
            merged.append(value)
    if len(merged) % 2:
        return None
    return [(merged[i], merged[i + 1]) for i in range(0, len(merged), 2)]


def _intersector(solid):
    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(solid, 1e-6)
    return intersector


def _pixels(points: np.ndarray, observed: verify.Observed, dims: dict[str, float]) -> np.ndarray:
    right, up, _ = verify.camera(observed.name)
    relative = points - np.array([dims["x"], dims["y"], dims["z"]]) / 2
    half = observed.image.shape[0] / 2
    return np.stack([relative @ right / observed.mm_per_px + half - 0.5,
                     half - 0.5 - relative @ up / observed.mm_per_px], axis=1)


def _closed_in_views(hole: Cylinder, index: int, observed: dict, dims: dict[str, float]) -> bool:
    """Whether every straight view along the hole's axis shows the part at the hole's centre."""
    seen = 0
    for name in verify.ORTHO_VIEWS:
        view = observed.get(name)
        if view is None or abs(verify.camera(name)[2][index]) < 0.999:
            continue
        x, y = _pixels(np.array([hole.centre]), view, dims)[0].round().astype(int)
        reach = max(1, int(0.5 * hole.radius / view.mm_per_px))
        patch = view.mask[max(0, y - reach):y + reach + 1, max(0, x - reach):x + reach + 1]
        if not patch.size or patch.mean() < 0.9:
            return False
        seen += 1
    return seen > 0


def _circle(centre: np.ndarray, axis: np.ndarray, radius: float, count: int = 180) -> np.ndarray:
    other = np.eye(3)[int(np.argmin(np.abs(axis)))]
    u = np.cross(axis, other)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    angle = np.linspace(0, 2 * np.pi, count, endpoint=False)
    return centre + radius * (np.outer(np.cos(angle), u) + np.outer(np.sin(angle), v))


def resolve(solid, holes: list[Cylinder], case_dir: Path | None) -> tuple[list[Cylinder], list[dict]]:
    """`holes` with every span that the views show closed replaced by its blind holes, and a note
    on each replacement. Holes along x, y or z only; everything else is returned as it is."""
    if not ENABLED or not holes or case_dir is None:
        return holes, []
    try:
        box = json.loads((case_dir / "facts.json").read_text())["bounding_box_mm"]
        dims = {k: float(box[k]) for k in "xyz"}
        observed = verify.observe(case_dir, dims)
    except Exception:
        return holes, []
    intersector = _intersector(solid)
    found = {}
    for number, hole in enumerate(holes):
        axis = np.array(hole.axis)
        index = int(np.argmax(np.abs(axis)))
        if abs(axis[index]) > 0.999 and not hole.keep and _closed_in_views(hole, index, observed, dims):
            ends = _ends(hole, np.sign(axis[index]) * np.eye(3)[index], intersector)
            if ends:
                found[number] = ends
    # the holes of one entry are one feature made several times: the shallowest room decides for all
    room = {}
    for number, ends in found.items():
        group = holes[number].group
        room[group] = min([room.get(group, np.inf)] + [limit for _, _, limit in ends])
    result, notes = [], []
    for number, hole in enumerate(holes):
        if number not in found:
            result.append(hole)
            continue
        depth = GUESS_SHARE * room[hole.group]
        for opening, inward, _ in found[number]:
            # start a little outside the face, so the opening is cut cleanly
            middle = opening + inward * (depth - 0.02) / 2
            result.append(replace(hole, centre=tuple(map(float, middle)), length=float(depth + 0.02)))
        notes.append({"diameter": 2 * hole.radius, "centre": hole.centre, "listed_length": hole.length,
                      "blind_holes": len(found[number]), "depth": round(float(depth), 2)})
    return result, notes


def _ends(hole: Cylinder, axis: np.ndarray, intersector) -> list[tuple[np.ndarray, np.ndarray, float]] | None:
    """If cutting `hole` as listed would open a line of sight through the solid: where a blind hole
    starts at each end of the span, which way it runs, and how far it can go before it breaks
    through its wall or meets the hole from the other side. None if the hole is fine as listed."""
    centre, half = np.array(hole.centre), hole.length / 2
    on_axis = None
    for probe in [centre] + list(_circle(centre, axis, 0.7 * hole.radius, 4)):
        stretches = _material(probe, axis, intersector)
        if stretches is None:
            return None
        if any(a < -half - 0.05 or b > half + 0.05 for a, b in stretches):
            return None  # material beyond an end: the hole is hidden behind it, as listed
        if on_axis is None:
            on_axis = stretches
    if not on_axis:
        return None
    first, last = on_axis[0], on_axis[-1]
    shared = 2 if len(on_axis) == 1 else 1
    ends = []
    for start, wall_end, inward in ((first[0], first[1], axis), (last[1], last[0], -axis)):
        limit = abs(wall_end - start) / shared
        if limit > 0.1:
            ends.append((centre + axis * start, inward, limit))
    return ends or None
