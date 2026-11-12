"""Step 3, deterministic: make round what the silhouettes left square.

A cylinder whose axis lies across two views is cut by each of them as a strip, so the visual hull has
a square where the part has a circle (4 / pi = 1.27 times the volume). The same holds for every
turned profile: cones, tori, spheres.

facts.json says where the axes are: every listed boss or bore gives a line along X, Y or Z. Along
such a line, wherever both side views show one strip of the same width centred on the line, the hull
is a square centred on the axis. It is cut down to the circle if there is a reason to believe the
part is round there:

- "boss": facts.json lists a boss of that radius on this axis, around that position;
- "turned": seen along the axis the part is a disc centred on it, and facts.json has only faces a
  lathe makes (planes, cylinders, cones, tori, spheres);
- "disc": seen along the axis the part is a disc centred on it, but it has other faces too (fins or
  grooves on a cone, practice part 6). This alone does not make a section round: a square body under
  a round flange looks the same. So it is cut separately, after the others, and kept only if the
  lines of the views fit the result at least as well as before: the corner edges of a square section
  that the views do not draw go away, those they do draw would be lost.

The profile that is revolved is the fitted outline of one side view, so cylinders, cones and tori
come out as such. The caller accepts the result only if the verifier does not rate it lower.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeRevol
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad import export
from agentic_cad import holes as hl
from agentic_cad import trace
from agentic_cad import verify
from agentic_cad import visual_hull as vh

TOLERANCE_PX = 1.5     # how far two views, or a view and facts.json, may disagree
MIN_SPAN_PX = 3.0      # shorter stretches along the axis are not worth a cut
SAMPLES = 4096         # positions along the axis at which the two side views are compared
DISC = os.environ.get("HULL_ROUNDS_DISC", "1") != "0"   # also propose round sections on the "disc" evidence
NEARLY_ROUND = 0.03    # the two side views may differ by this share of the width where only the disc speaks for round
DISC_OVERLAP = 0.985   # the end view counts as a disc when it overlaps one this well
LATHE_FACES = {"PLANE", "CYLINDER", "CONE", "TORUS", "SPHERE"}
MAX_AXES = 24          # more axis lines than this are not looked at (largest bosses first)
TIMEOUT = float(os.environ.get("HULL_ROUNDS_TIMEOUT", 40))
SCORE_SLACK = 0.003    # the verifier's score may drop by this much through a cut and still pass
LINES_SLACK = 0.002    # ... and its line term, for a cut that only the disc seen along the axis speaks for


@dataclass
class Round:
    axis: str                         # "x", "y" or "z"
    position: dict[str, float]        # where the axis is: mm on the two other box axes
    silhouette: vh.Silhouette         # the side view whose outline is revolved
    spans: list[tuple[float, float]]  # stretches along the axis (mm) to make round
    reach: float                      # half-width of the square that is cut down, a little more than the profile
    evidence: str                     # "boss", "turned" or "disc"


def _profile(silhouette: vh.Silhouette, axis: str, dims: dict[str, float]):
    """For SAMPLES positions along `axis`: lower and upper end (mm) of the part across the axis in
    this view, whether the part is one run there, and the size of a pixel."""
    spec = vh.ORTHO[silhouette.name]
    mask = silhouette.mask > 0
    if spec["u"] == axis:   # the axis runs along the columns
        runs, across = mask.T[:, ::-1], spec["v"]
    else:                   # along the rows, upwards
        runs, across = mask[::-1, :], spec["u"]
    count_t, count_a = runs.shape
    counts = runs.sum(axis=1)
    first = runs.argmax(axis=1)
    last = count_a - 1 - runs[:, ::-1].argmax(axis=1)
    single = (counts > 0) & (counts == last - first + 1)
    pixel = dims[across] / (count_a - 1)
    index = np.round(np.linspace(0, count_t - 1, SAMPLES)).astype(int)
    return first[index] * pixel, last[index] * pixel, single[index], max(pixel, dims[axis] / (count_t - 1)), across


def _is_disc(silhouette: vh.Silhouette, centre: tuple[float, float], radius: float, dims: dict[str, float]) -> bool:
    """Does the view along the axis show a disc of this radius around the axis (holes aside)?"""
    spec = vh.ORTHO[silhouette.name]
    mask = (silhouette.mask > 0).astype(np.uint8)
    height, width = mask.shape
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 1, cv2.FILLED)
    cols = np.arange(width)[None, :] / (width - 1) * dims[spec["u"]]
    rows = (height - 1 - np.arange(height))[:, None] / (height - 1) * dims[spec["v"]]
    disc = (cols - centre[0]) ** 2 + (rows - centre[1]) ** 2 <= radius ** 2
    union = (disc | (filled > 0)).sum()
    return bool(union) and (disc & (filled > 0)).sum() / union >= DISC_OVERLAP


def _stretches(flags: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges [start, end] where `flags` is set."""
    padded = np.concatenate([[False], flags, [False]])
    change = np.nonzero(padded[1:] != padded[:-1])[0]
    return [(int(a), int(b) - 1) for a, b in zip(change[::2], change[1::2])]


def _corner_positions(silhouette: vh.Silhouette, axis: str) -> np.ndarray:
    """Where along the axis the fitted outline has its corners: spans end exactly there."""
    index = 0 if vh.ORTHO[silhouette.name]["u"] == axis else 1
    values = [point[index] for outline in silhouette.outlines for loop in (outline["outer"], *outline["holes"])
              for segment in loop if segment[0] != "circle" for point in (segment[1], segment[-1])]
    return np.array(sorted(set(values)), dtype=float)


def find(case_dir: Path, views: list[Path], dims: dict[str, float]) -> list[Round]:
    """The stretches of the part that are round according to facts.json and the views."""
    try:
        facts = json.loads((case_dir / "facts.json").read_text())
    except Exception:
        return []
    bosses = hl._cylinders(facts.get("cylindrical_shafts_and_bosses"))
    bores = hl._cylinders(facts.get("cylindrical_holes_and_bores"))
    lengths = {}  # facts.json group -> longest listed length: a boss is at most that long
    for kind, entries in (("boss", facts.get("cylindrical_shafts_and_bosses")), ("bore", facts.get("cylindrical_holes_and_bores"))):
        for group, entry in enumerate(entries or []):
            if entry.get("lengths"):
                lengths[kind, group] = float(max(entry["lengths"]))
    lathe_only = set(facts.get("face_types") or {"?"}) <= LATHE_FACES
    letter_index = {"x": 0, "y": 1, "z": 2}

    # axis lines: (axis letter, position on the two other axes) -> bosses on it
    lines: dict[tuple, list] = {}
    for kind, cylinders in (("boss", bosses), ("bore", bores)):
        for cylinder in cylinders:
            letters = [l for l in "xyz" if abs(cylinder.axis[letter_index[l]]) > 0.9999]
            if not letters:
                continue
            axis = letters[0]
            others = [l for l in "xyz" if l != axis]
            key = (axis, *(round(cylinder.centre[letter_index[l]], 1) for l in others))
            entry = lines.setdefault(key, {"position": {l: cylinder.centre[letter_index[l]] for l in others},
                                           "bosses": [], "largest": 0.0})
            entry["largest"] = max(entry["largest"], cylinder.radius)
            if kind == "boss":
                reach = lengths.get((kind, cylinder.group), cylinder.length) / 2
                centre = cylinder.centre[letter_index[axis]]
                entry["bosses"].append((cylinder.radius, centre - reach, centre + reach))
    ordered = sorted(lines.items(), key=lambda item: -item[1]["largest"])[:MAX_AXES]

    silhouettes: dict[str, vh.Silhouette | None] = {}

    def silhouette(name: str) -> vh.Silhouette | None:
        if name not in silhouettes:
            spec = vh.ORTHO[name]
            try:
                silhouettes[name] = vh.ortho_silhouette(name, views, dims, hl.outline_hints(
                    case_dir, spec["u"], spec["v"], spec["along"]))
            except Exception:
                silhouettes[name] = None
        return silhouettes[name]

    rounds = []
    grid = np.linspace(0, 1, SAMPLES)
    for (axis, *_), line in ordered:
        side = [silhouette(name) for name, spec in vh.ORTHO.items() if spec["along"] != axis]
        end = silhouette(next(name for name, spec in vh.ORTHO.items() if spec["along"] == axis))
        if any(s is None for s in side):
            continue
        profiles = [_profile(s, axis, dims) for s in side]
        tolerance = TOLERANCE_PX * max(p[3] for p in profiles)
        valid = np.ones(SAMPLES, bool)
        widths = []
        for low, high, single, _, across in profiles:
            valid &= single & (np.abs((low + high) / 2 - line["position"][across]) <= tolerance)
            widths.append((high - low) / 2)
        # Where the profile is steep, a pixel's shift along the axis changes the width by many pixels:
        # the two views agree if their widths do anywhere within a pixel and a half along the axis
        window = np.ones((2 * int(np.ceil(tolerance / (dims[axis] / (SAMPLES - 1)))) + 1, 1), np.uint8)
        low_0, high_0, low_1, high_1 = (
            operation(w.astype(np.float32).reshape(-1, 1), window, borderType=cv2.BORDER_REPLICATE).ravel()
            for w in widths for operation in (cv2.erode, cv2.dilate))
        # for the "disc" evidence below: the views may differ by a little more, as they do where fins
        # stand on a round body (practice part 6: 1 mm in 60)
        slack = np.maximum(tolerance, NEARLY_ROUND * np.maximum(widths[0], widths[1]))
        loose = valid & (low_0 <= high_1 + slack) & (low_1 <= high_0 + slack)
        valid &= (low_0 <= high_1 + tolerance) & (low_1 <= high_0 + tolerance)
        if not loose.any():
            continue
        width = (widths[0] + widths[1]) / 2
        position = grid * dims[axis]

        accepted = np.zeros(SAMPLES, bool)
        for radius, start, stop in line["bosses"]:
            accepted |= valid & (np.abs(width - radius) <= tolerance) & (position >= start - tolerance) & (position <= stop + tolerance)
        versions = [(accepted, "boss")]
        if end is not None and all(abs(c.axis[letter_index[axis]]) > 0.9999 for c in bosses):
            spec = vh.ORTHO[end.name]
            centre = (line["position"][spec["u"]], line["position"][spec["v"]])
            if _is_disc(end, centre, float(np.maximum(widths[0], widths[1])[loose].max()), dims):
                if lathe_only and valid.any():
                    versions = [(valid.copy(), "turned")]
                elif DISC and (loose & ~accepted).any():
                    # Seen along the axis the part is a disc, but it has faces a lathe does not make:
                    # everything the two side views nearly agree on is proposed as a second, weaker cut
                    versions.append((loose & ~accepted, "disc"))

        pixel = min(p[3] for p in profiles)
        for accepted, evidence in versions:
            if not accepted.any():
                continue
            # Revolve the outline of the sharper of the two side views; for the disc version that of
            # the wider one, so that nothing either view shows is cut.
            if evidence == "disc":
                chosen = side[int(np.argmax([w[accepted].mean() for w in widths]))]
            else:
                chosen = side[int(np.argmin([p[3] for p in profiles]))]
            corners = _corner_positions(chosen, axis)
            spans = []
            for first, last in _stretches(accepted):
                start, stop = float(position[first]), float(position[last])
                if len(corners):
                    for value in (start, stop):
                        nearest = float(corners[np.argmin(np.abs(corners - value))])
                        if abs(nearest - value) <= TOLERANCE_PX * pixel:
                            start, stop = (nearest, stop) if value == start else (start, nearest)
                if stop - start >= MIN_SPAN_PX * pixel:
                    spans.append((max(0.0, start), min(dims[axis], stop)))
            if spans:
                rounds.append(Round(axis, dict(line["position"]), chosen, spans,
                                    float(np.maximum(widths[0], widths[1])[accepted].max()) + 4 * tolerance, evidence))
    return rounds


def _faces(shape: TopoDS_Shape) -> list:
    result, explorer = [], TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        result.append(TopoDS.Face(explorer.Current()))
        explorer.Next()
    return result


def _tool(item: Round) -> list[TopoDS_Shape]:
    """What to remove for one axis line: per span, a square bar around the axis minus the revolved profile."""
    spec = vh.ORTHO[item.silhouette.name]
    across = spec["v"] if spec["u"] == item.axis else spec["u"]
    # put the outline in the plane through the axis, so that it can be revolved about it
    plane = dataclasses.replace(item.silhouette, origin=vh.UNIT[spec["along"]] * item.position[spec["along"]])
    centre = item.position[across]

    def point(along_axis: float, across_axis: float) -> gp_Pnt:
        a, b = (along_axis, across_axis) if spec["u"] == item.axis else (across_axis, along_axis)
        return gp_Pnt(*map(float, plane.origin + a * plane.right + b * plane.up))

    outline_faces = [BRepBuilderAPI_MakeFace(vh._wire(outline["outer"], plane, True), True).Face()
                     for outline in item.silhouette.outlines]
    direction = gp_Dir(*map(float, vh.UNIT[item.axis]))
    tools = []
    for start, stop in item.spans:
        polygon = BRepBuilderAPI_MakePolygon()
        for corner in ((start, centre), (stop, centre), (stop, centre + item.reach), (start, centre + item.reach)):
            polygon.Add(point(*corner))
        polygon.Close()
        window = BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face()
        keep = []
        for face in outline_faces:
            common = BRepAlgoAPI_Common(face, window)
            if common.IsDone():
                keep += [BRepPrimAPI_MakeRevol(half, gp_Ax1(point(start, centre), direction)).Shape()
                         for half in _faces(common.Shape())]
        if not keep:
            continue
        low = {item.axis: start, **{l: item.position[l] - item.reach for l in item.position}}
        size = {item.axis: stop - start, **{l: 2 * item.reach for l in item.position}}
        bar = BRepPrimAPI_MakeBox(gp_Pnt(low["x"], low["y"], low["z"]), size["x"], size["y"], size["z"]).Shape()
        arguments, pieces = List_TopoDS_Shape(), List_TopoDS_Shape()
        arguments.Append(bar)
        for solid in keep:
            pieces.Append(solid)
        operation = BRepAlgoAPI_Cut()
        operation.SetArguments(arguments)
        operation.SetTools(pieces)
        operation.Build()
        if operation.IsDone():
            tools.append(operation.Shape())
    return tools


def cut_rounds(shape: TopoDS_Shape, rounds: list[Round]) -> TopoDS_Shape | None:
    """Make the listed stretches of `shape` round. One valid solid, or None. Runs in a child process.

    The result must also survive being written as a .stp: a revolved profile can leave a face that
    is valid here and broken in the file. If all axis lines together do not, they are cut one at a
    time and only those that do are kept."""
    solid = _cut(shape, rounds)
    if solid is not None and export.reads_back_valid(solid):
        return solid
    if len(rounds) < 2:
        return None
    current, kept = shape, 0
    for item in rounds:
        attempt = _cut(current, [item])
        if attempt is not None and export.reads_back_valid(attempt):
            current, kept = attempt, kept + 1
    return current if kept else None


def _cut(shape: TopoDS_Shape, rounds: list[Round]) -> TopoDS_Shape | None:
    tools = List_TopoDS_Shape()
    for item in rounds:
        for tool in _tool(item):
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
    """The pipeline step: find the round stretches, cut them, keep what the verifier accepts."""
    with trace.step("cut_rounds", kind="code") as step:
        if case_dir is None:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        rounds = find(case_dir, views, dims)
        step.metric(axes=len(rounds))
        if not rounds:
            step.log("no stretch of the part is known to be round")
            return solid
        step.attach("rounds.json", [{"axis": r.axis, "position": r.position, "spans": r.spans,
                                     "evidence": r.evidence, "profile_from": r.silhouette.name} for r in rounds])
        target = vh.read_volume(case_dir)
        observed = verify.observe(case_dir, dims)
        before = verify.check(solid, case_dir, observed).score
        weak = [r for r in rounds if r.evidence == "disc"]
        rounds = [r for r in rounds if r.evidence != "disc"]

        def attempt(current, items, score, timeout):
            carved = agent._guarded(step, current, target, lambda: agent.cutter.run(cut_rounds, current, items, timeout), quiet=True)
            if carved is None or vh.volume(carved) >= vh.volume(current) * (1 - 1e-6):
                return None, score
            after = verify.check(carved, case_dir, observed).score
            if after < score - SCORE_SLACK:
                step.log(f"the verifier rates the result lower ({score:.3f} to {after:.3f}); not kept", level="warning")
                return None, score
            return carved, after

        carved, score = attempt(solid, rounds, before, TIMEOUT) if rounds else (None, before)
        kept = len(rounds)
        if carved is None and len(rounds) > 1:
            step.log("cutting all axes at once was refused; trying one at a time")
            carved, kept = solid, 0
            for item in rounds[:6]:
                result, score = attempt(carved, [item], score, TIMEOUT / 2)
                if result is not None:
                    carved, kept = result, kept + 1
        if carved is None or kept == 0:
            carved, kept = solid, 0
        for item in weak[:3]:   # the sections only the disc speaks for: the views' lines must not fit worse
            if agent.out_of_time():
                break
            current = carved
            lines = verify.check(current, case_dir, observed).mean("lines")
            result = agent._guarded(step, current, target, lambda: agent.cutter.run(cut_rounds, current, [item], TIMEOUT), quiet=True)
            if result is None or vh.volume(result) >= vh.volume(current) * (1 - 1e-6):
                continue
            report = verify.check(result, case_dir, observed)
            if report.mean("lines") < lines - LINES_SLACK or report.score < score - SCORE_SLACK:
                step.log(f"round on the disc's word alone: the lines fit worse ({lines:.3f} to {report.mean('lines'):.3f}); not kept",
                         level="warning")
                continue
            step.log(f"made round on the disc's word: lines {lines:.3f} to {report.mean('lines'):.3f}")
            carved, score, kept = result, report.score, kept + 1
        if kept == 0:
            step.log("nothing was made round")
            return solid
        step._status = None
        step.metric(axes_cut=kept, score_before=round(before, 4), score_after=round(score, 4))
        agent._record(step, carved, "rounds")
        return carved
