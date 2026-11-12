"""Deterministic: the empty middle of a part wound or stacked around an axis.

A coil spring seen from the side is a zigzag, and the solid two such silhouettes leave is a
stack of slabs, one per turn, right across the coil (practice part 18). The view along the axis
shows an end plate, so nothing looks down the middle, and the middle is no listed hole: it has
no cylindrical face. It stays filled, with three times the part's volume.

Two things point to it. facts.json lists the outside as a cylinder along an axis, and our
solid, followed along that axis at the rim of that cylinder, is not closed all the way round:
the silhouettes have cut gaps into it. Where it is closed all round from an end on, that is an
end of the part (a plate, a flange) and stays. Through the gaps the side views see the far side of the part, so what
is taken out of the middle shows.

Cylinders around the axis are tried, the widest first, from one end of the part to the other. A cylinder is kept if it leaves the part's volume, the outline fits no worse and the
local judge finds the eight views fitting strictly better than before; the widest such one is
taken. A solid shaft is never tried: our solid is closed all round it. A middle that no view
sees through is never kept: nothing changes in the views.

The judge stops short of the true middle on the spring (20 of about 22 mm): beyond that the
flat sides of our slabs show where the views have a round wire.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopAbs import TopAbs_IN
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import judge, trace, verify
from agentic_cad import visual_hull as vh

ENABLED = os.environ.get("HULL_CORE", "1") != "0"
MIN_EXCESS = 1.05          # nothing is looked for in a solid within this of the part's volume
SHARES = (0.9, 0.85, 0.8, 0.75, 0.7, 0.6, 0.5)   # of the listed radius: the cylinders tried, the widest first
PROBE = 0.97               # our solid is followed along the axis at this share of the listed radius: at its rim,
                           # where what is wound around the axis is thinnest and the ends stand clear of it
LINES = 12                 # ... on this many lines around the axis
STATIONS = 120             # ... at this many places along it
MIN_GAP = 0.1              # our solid is open somewhere around the axis over at least this share of its length
TIMEOUT = float(os.environ.get("HULL_CORE_TIMEOUT", 60))
AXES = {"X": 0, "Y": 1, "Z": 2}


def bore(shape: TopoDS_Shape, cylinder: tuple) -> TopoDS_Shape | None:
    """`shape` without a cylinder (start point, direction, radius, length). Runs in a child process."""
    start, direction, radius, length = cylinder
    tool = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*start), gp_Dir(*direction)), radius, length).Shape()
    cut = BRepAlgoAPI_Cut(shape, tool)
    if not cut.IsDone():
        return None
    result = vh.largest_solid(cut.Shape())
    if result is None or vh.volume(result) <= 0 or not BRepCheck_Analyzer(result).IsValid():
        return None
    return result


def listed(case_dir: Path) -> list[tuple[int, np.ndarray, float, float]]:
    """The widest cylinder facts.json lists along each of the three axes: axis, centre, radius, length."""
    try:
        entries = json.loads((case_dir / "facts.json").read_text()).get("cylindrical_shafts_and_bosses", [])
    except Exception:
        return []
    widest: dict[int, tuple] = {}
    for entry in entries:
        axis = AXES.get(str(entry.get("axis")).strip().upper())
        if axis is None or not entry.get("centres") or not entry.get("lengths"):
            continue
        radius, length = float(entry.get("diameter", 0)) / 2, float(max(entry["lengths"]))
        if radius > 0 and length > 0 and (axis not in widest or radius > widest[axis][2]):
            widest[axis] = (axis, np.array(entry["centres"][0], dtype=float), radius, length)
    return sorted(widest.values(), key=lambda item: -item[2])


def all_round(solid: TopoDS_Shape, axis: int, centre: np.ndarray, radius: float, low: float, high: float) -> tuple[np.ndarray, np.ndarray]:
    """Along the axis, at `radius` from it: the places looked at, and at each whether our solid is
    there all the way round (on every line around the axis)."""
    classifier = BRepClass3d_SolidClassifier(solid)
    others = [k for k in range(3) if k != axis]
    half = (high - low) / (2 * STATIONS)
    stations = np.linspace(low + half, high - half, STATIONS)   # not on the end faces themselves
    inside = np.zeros(STATIONS, int)
    for angle in np.arange(LINES) * 2 * np.pi / LINES:
        point = centre.copy()
        point[others[0]] += radius * np.cos(angle)
        point[others[1]] += radius * np.sin(angle)
        for index, station in enumerate(stations):
            point[axis] = station
            classifier.Perform(gp_Pnt(*map(float, point)), 1e-6)
            inside[index] += classifier.State() == TopAbs_IN
    return stations, inside == LINES


def between_ends(stations: np.ndarray, closed: np.ndarray) -> tuple[float, float] | None:
    """The stretch of the axis between the part's two ends: an end is where our solid is there all
    the way round from the very end of the cylinder on (an end plate, a flange); between them it
    must be open somewhere around the axis for a real share of the length, or nothing says the
    part is not one body."""
    first = 0
    while first < len(closed) and closed[first]:
        first += 1
    last = len(closed) - 1
    while last >= 0 and closed[last]:
        last -= 1
    if first > last or (~closed[first:last + 1]).sum() < MIN_GAP * len(closed):
        return None
    half = (stations[1] - stations[0]) / 2
    return float(stations[first] - half), float(stations[last] + half)


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step: take out the middle of a part our solid has in pieces along a listed axis."""
    with trace.step("open_core", kind="code") as step:
        if case_dir is None or not ENABLED:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        if target and vh.volume(solid) < MIN_EXCESS * target:
            step.log("the solid already has the part's volume; nothing to look for")
            return solid
        candidates = listed(case_dir)
        if not candidates:
            step.log("facts.json lists no cylinder along x, y or z")
            return solid
        rater, kept = None, 0
        for axis, centre, radius, length in candidates:
            name = "xyz"[axis]
            low, high = max(0.0, centre[axis] - length / 2), min(dims[name], centre[axis] + length / 2)
            span = between_ends(*all_round(solid, axis, centre, PROBE * radius, low, high))
            if span is None:
                step.log(f"along {name} our solid is closed all the way round the rim of the listed cylinder: nothing says its middle is empty")
                continue
            start, end = span   # the ends stay
            for share in SHARES:
                if agent.out_of_time():
                    step.log("out of time for this case; no further cylinder is tried", level="warning")
                    break
                origin = centre.copy()
                origin[axis] = start
                direction = np.zeros(3)
                direction[axis] = 1.0
                cylinder = (tuple(map(float, origin)), tuple(direction), float(share * radius), float(end - start))
                current = solid
                carved = agent._guarded(step, current, target, lambda: agent.cutter.run(bore, current, cylinder, TIMEOUT), quiet=True)
                text = f"along {name}, {2 * share * radius:.1f} mm wide from {start:.1f} to {end:.1f} mm"
                if carved is None or vh.volume(carved) >= vh.volume(current) * (1 - 1e-6):
                    step.log(f"{text}: no valid cut, nothing removed, or less than the part's volume left")
                    continue
                if rater is None:
                    rater = judge.Judge(case_dir, dims, agent.evidence(case_dir, dims).observed)
                verdict = rater.rate(current, carved)
                judge.note(agent, "core", current, carved, bool(verdict.accepted and verdict.error < 0), verdict)
                if not verdict.accepted or verdict.error >= 0:
                    step.log(f"{text}: {verdict.reason or 'no view shows it'} ({verdict})")
                    continue
                if not agent.save(carved):
                    step.log(f"{text}: the file does not read back as one valid solid")
                    continue
                step.log(f"{text}: kept ({verdict})")
                solid, kept = carved, kept + 1
                break   # the widest cylinder the views take
        step.metric(cores=kept)
        if kept == 0:
            return solid
        step.metric(volume_cm3=round(vh.volume(solid) / 1000, 3), **({"vs_true_volume": round(vh.volume(solid) / target, 3)} if target else {}))
        step._status = None
        agent._record(step, solid, "core")
        return solid
