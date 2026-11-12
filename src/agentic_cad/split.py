"""Last step, deterministic: split a face of the solid where the views draw a line across it.

The steps before this one only shape the solid. Where a view shows a line through the inside of one
of our faces and the isometric view confirms that the line lies on our surface (lines.py: "level" or
"step", the seam between two faces of one surface or the rim of something that starts there), the
part has two faces where we have one. The line is then drawn onto the face as an edge.

No material moves: the volume stays what it was. Pieces whose ends meet are joined into one figure
(the outline of a pocket, a letter), and a figure is drawn as the intersection of the face with the
figure swept along the view direction, so its edges lie exactly on the face. A figure that neither
closes on itself nor reaches the border of the face at both ends splits nothing; OpenCascade drops it.

The step works on the solid with its same-surface faces already merged (visual_hull.tidy), and the
export must not merge them again: it sets `agent.keep_faces`. It does not look at how many faces
facts.json counts: a solid can have too many faces in one place and still lack this one (practice
part 1, the line across the bottom).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import (GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Sphere, GeomAbs_Torus)
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeVertex, BRepBuilderAPI_MakeWire
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepFeat import BRepFeat_SplitShape
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.GC import GC_MakeArcOfCircle, GC_MakeCircle
from OCP.gp import gp_Pnt, gp_Vec
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from agentic_cad import lines, shading, trace, verify
from agentic_cad import visual_hull as vh

ON_SURFACE = ("level", "step")   # verdicts of lines.py for a line that lies on our surface
REACH_PX = 8.0        # a piece is carried on this far at both ends, to reach the border of the face
MIN_LENGTH_PX = 20.0  # shorter figures are not drawn
MIN_CONFIRMED = 0.5   # share of a figure's length the isometric view must confirm to lie on our surface
JOIN_PX = 8.0         # two pieces whose ends lie this close meet there
NEAR_PX = 6.0         # an edge drawn onto the face lies this close to where the view shows the line
REVOLVED = (GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus)
MERIDIAN_DEG = 3.0    # a line whose points lie within this angle around the axis runs from end to end of the face
SAME_SEAM_DEG = 10.0  # such lines this close around the axis are one line
TOUCH_MM = 1e-4       # two points this close are the same point of the solid
SAME_PX = 3.0         # two pieces whose points lie this close in space are the same edge seen twice
MAX_PIECES = 60       # figures drawn per case, the longest first
TIMEOUT = float(os.environ.get("HULL_SPLIT_TIMEOUT", 20))
# No clock of its own: MAX_PIECES bounds what is drawn, so a slower machine gives the same solid.
SCORE_SLACK = 0.0     # the verifier must not rate the split solid lower


def faces_of(shape: TopoDS_Shape) -> list:
    result, explorer = [], TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        result.append(TopoDS.Face(explorer.Current()))
        explorer.Next()
    return result


def _point(values) -> gp_Pnt:
    return gp_Pnt(float(values[0]), float(values[1]), float(values[2]))


def _edge(kind: str, points: list):
    points = [_point(p) for p in points]
    if kind == "line":
        maker = BRepBuilderAPI_MakeEdge(points[0], points[1])
    else:
        curve = (GC_MakeCircle if kind == "circle" else GC_MakeArcOfCircle)(points[0], points[1], points[2])
        if not curve.IsDone():
            return None
        maker = BRepBuilderAPI_MakeEdge(curve.Value())
    return maker.Edge() if maker.IsDone() else None


def _sweep(job: dict) -> TopoDS_Shape | None:
    """The figure swept along the view direction, right through the part. Its pieces go in as one
    wire: only then do the edges it leaves on the face share their corners."""
    wire = BRepBuilderAPI_MakeWire()
    for kind, points in job["pieces"]:
        edge = _edge(kind, points)
        if edge is None:
            return None
        wire.Add(edge)
        if not wire.IsDone():
            return None
    prism = BRepPrimAPI_MakePrism(wire.Wire(), gp_Vec(*[float(v) for v in job["sweep"]]))
    return prism.Shape() if prism.IsDone() else None


def _draw(splitter, face, tool, job: dict) -> int:
    """Add to the splitter the edges a swept figure leaves on one face; how many."""
    section = BRepAlgoAPI_Section(face, tool)
    section.Build()
    if not section.IsDone():
        return 0
    near, found = np.array(job["near"]), []
    explorer = TopExp_Explorer(section.Shape(), TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        explorer.Next()
        # The sweep also meets the face where it turns away from the camera (the far side of a
        # cylinder): only what lies where the view shows the line is drawn.
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        middle = curve.Value((first + last) / 2)
        if np.linalg.norm(near - [middle.X(), middle.Y(), middle.Z()], axis=1).min() > job["tolerance"]:
            continue
        ends = [np.array([q.X(), q.Y(), q.Z()]) for q in (curve.Value(first), curve.Value(last))]
        found.append((edge, ends))
    # An edge with an end in the open face splits nothing, and with one such edge among them
    # OpenCascade drops the others too (a line carried on past the figure it ends on leaves a stub
    # on the far side). Kept: edges whose ends lie on the border of the face or on another kept edge.
    wires, explorer = [], TopExp_Explorer(face, TopAbs_WIRE)
    while explorer.More():
        wires.append(explorer.Current())
        explorer.Next()

    def on_border(point: np.ndarray) -> bool:
        vertex = BRepBuilderAPI_MakeVertex(_point(point)).Vertex()
        for wire in wires:
            distance = BRepExtrema_DistShapeShape(vertex, wire)
            if distance.IsDone() and distance.Value() <= TOUCH_MM:
                return True
        return False

    anchored = [[on_border(end) for end in ends] for _, ends in found]
    keep = [True] * len(found)
    changed = True
    while changed:
        changed = False
        for i, (_, ends) in enumerate(found):
            if not keep[i] or np.linalg.norm(ends[0] - ends[1]) <= TOUCH_MM:   # a closed edge needs no anchor
                continue
            for which, end in enumerate(ends):
                held = anchored[i][which] or any(
                    keep[j] and j != i and min(np.linalg.norm(end - other) for other in found[j][1]) <= TOUCH_MM
                    for j in range(len(found)))
                if not held:
                    keep[i], changed = False, True
                    break
    for (edge, _), kept in zip(found, keep):
        if kept:
            splitter.Add(edge, face)
    return sum(keep)


def imprint(shape: TopoDS_Shape, jobs: list[dict]) -> TopoDS_Shape | None:
    """Draw every job's figure onto its face. Runs in the child process (vh.Cutter).

    The figures of one face are drawn one after the other, each onto what the earlier ones made of
    that face. A figure that ends on another one (the stem of a T, a pocket that shares a side with
    its neighbour) is an open line on the whole face and would split nothing; on the piece of the
    face the earlier figure left, it runs from border to border."""
    faces = faces_of(shape)
    pieces_of_face: dict[int, list] = {}   # what has become of each face that is drawn on
    queues: dict[int, list] = {}
    for job in jobs:
        if job["face"] < len(faces):
            queues.setdefault(job["face"], []).append(job)
            pieces_of_face[job["face"]] = [faces[job["face"]]]
    drawn = False
    while any(queues.values()):
        splitter, added = BRepFeat_SplitShape(shape), 0
        for index, queue in queues.items():
            if not queue:
                continue
            job = queue.pop(0)
            try:
                tool = _sweep(job)
            except Exception:
                tool = None
            if tool is None:
                continue
            here = sum(_draw(splitter, face, tool, job) for face in pieces_of_face[index])
            if not here and not job.get("again"):
                # nothing to hold on to yet: once more after the other figures of this face
                queue.append({**job, "again": True})
            added += here
        if not added:
            continue
        try:
            splitter.Build()
            result = splitter.Shape()
        except Exception:
            continue
        if result is None or result.IsNull() or not BRepCheck_Analyzer(result).IsValid():
            continue   # this round is left out; the next is drawn on the shape as it was
        for index, parts in pieces_of_face.items():
            after = []
            for face in parts:
                made = [TopoDS.Face(item) for item in splitter.Modified(face)]
                after += made or [face]
            pieces_of_face[index] = after
        shape, drawn = result, True
    return shape if drawn else None


def _meeting(first: lines.Piece, second: lines.Piece, around: np.ndarray) -> np.ndarray:
    """Where two pieces that end near `around` meet: the crossing of two straight ones, else that point."""
    if first.kind == second.kind == "line":
        a, b = first.points[0], first.points[-1] - first.points[0]
        c, d = second.points[0], second.points[-1] - second.points[0]
        cross = b[0] * d[1] - b[1] * d[0]
        if abs(cross) > 1e-6 * np.hypot(*b) * np.hypot(*d):
            t = ((c[0] - a[0]) * d[1] - (c[1] - a[1]) * d[0]) / cross
            point = a + t * b
            if np.hypot(*(point - around)) <= JOIN_PX:
                return point
    return around


def figures(pieces: list[lines.Piece]) -> list[list[tuple]]:
    """Pieces of one face in one view, joined where their ends meet: each figure is a run of
    (piece, start, end) in pixels, end to start, closed or open. Where three or more ends meet, the
    two longest pieces are joined and the others end there. Open ends are carried on a little: to
    reach the border of the face, or the figure they end on (which `imprint` draws first when it is
    the longer one)."""
    ends = [(index, which, piece.points[0 if which == 0 else -1]) for index, piece in enumerate(pieces)
            if piece.kind != "circle" for which in (0, 1)]
    group = list(range(len(ends)))   # ends that lie close together, by the lowest of their numbers

    def root(i: int) -> int:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            if ends[i][0] != ends[j][0] and np.hypot(*(ends[i][2] - ends[j][2])) <= JOIN_PX:
                group[root(j)] = root(i)
    clusters: dict[int, list[int]] = {}
    for i in range(len(ends)):
        clusters.setdefault(root(i), []).append(i)
    joined = {}   # (piece, end) -> (other piece, other end, common point)
    for members in clusters.values():
        members.sort(key=lambda i: -pieces[ends[i][0]].length)
        first = members[0]
        second = next((i for i in members[1:] if ends[i][0] != ends[first][0]), None)
        if second is None:
            continue
        around = (ends[first][2] + ends[second][2]) / 2
        point = _meeting(pieces[ends[first][0]], pieces[ends[second][0]], around)
        joined[ends[first][:2]] = (ends[second][0], ends[second][1], point)
        joined[ends[second][:2]] = (ends[first][0], ends[first][1], point)
    result, used = [], set()

    def free_end(piece: lines.Piece, which: int) -> np.ndarray:
        point = piece.points[0 if which == 0 else -1]
        if piece.kind == "line":
            along = piece.points[-1] - piece.points[0]
            along = along / max(float(np.hypot(*along)), 1e-9)
            return point + (REACH_PX if which else -REACH_PX) * along
        # an arc is carried on along its circle, away from its middle
        centre = np.array(piece.centre)
        middle = piece.points[len(piece.points) // 2]
        angle = np.arctan2(point[1] - centre[1], point[0] - centre[0])
        turn = np.angle(np.exp(1j * (angle - np.arctan2(middle[1] - centre[1], middle[0] - centre[0]))))
        angle += np.sign(turn) * min(REACH_PX / max(piece.radius, 1e-9), 0.3)
        return centre + piece.radius * np.array([np.cos(angle), np.sin(angle)])

    for index, piece in enumerate(pieces):
        if index in used:
            continue
        if piece.kind == "circle":
            used.add(index)
            result.append([(piece, None, None)])
            continue
        # walk back to the start of the run this piece is in (or once round, if it is closed)
        current, entry = index, 0
        while (current, entry) in joined and joined[(current, entry)][0] != index:
            other, other_end, _ = joined[(current, entry)]
            current, entry = other, 1 - other_end
        run = []
        while current not in used:
            used.add(current)
            piece_now = pieces[current]
            start = joined[(current, entry)][2] if (current, entry) in joined else free_end(piece_now, entry)
            leave = 1 - entry
            end = joined[(current, leave)][2] if (current, leave) in joined else free_end(piece_now, leave)
            run.append((piece_now, start, end))
            if (current, leave) not in joined:
                break
            other, other_end, _ = joined[(current, leave)]
            current, entry = other, other_end
        result.append(run)
    return result


def job_for(figure: list[tuple], observed: verify.Observed, drawing: verify.Drawing, dims: dict) -> dict | None:
    """A figure as three-dimensional pieces behind the part, the vector that sweeps them through it,
    and points of our surface under it."""
    size = observed.mask.shape[0]
    diagonal = float(np.linalg.norm([dims["x"], dims["y"], dims["z"]]))
    centre_mm = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    view = figure[0][0].view

    def behind(pixels: np.ndarray) -> list:
        points = lines.lift(view, pixels, np.full(len(pixels), -diagonal), dims, observed.mm_per_px, size)
        return (points + centre_mm).tolist()

    parts, near = [], []
    for piece, start, end in figure:
        if piece.kind == "line":
            if np.hypot(*(end - start)) < 1.0:
                return None
            parts.append(("line", behind(np.array([start, end]))))
        else:
            centre = np.array(piece.centre)
            if piece.kind == "circle":
                angles = np.array([0.0, 2 * np.pi / 3, 4 * np.pi / 3])
                pixels = centre + piece.radius * np.stack([np.cos(angles), np.sin(angles)], axis=1)
            else:
                pixels = np.array([start, piece.points[len(piece.points) // 2], end])
            parts.append((piece.kind, behind(pixels)))
        depth, inside = lines._at(drawing.depth, piece.points)
        usable = inside & np.isfinite(depth)
        if usable.any():
            near.append(lines.lift(view, piece.points[usable], depth[usable], dims, observed.mm_per_px, size) + centre_mm)
    if not near:
        return None
    # entered from the far end, a piece runs backwards: its own points are in the order of the walk already
    return {"face": figure[0][0].face, "pieces": parts, "near": np.vstack(near)[::2].tolist(),
            "tolerance": NEAR_PX * observed.mm_per_px, "sweep": (2 * diagonal * verify.camera(view)[2]).tolist()}


def seams(found: dict[str, list[lines.Piece]], observed: dict, drawings, dims: dict, faces: list) -> set[int]:
    """The pieces (by id) that are the seam of a face of revolution, not a boundary between two faces.

    A cylinder, cone or torus that goes all the way round is one face, closed along one line from end
    to end, and a render draws that line. Ours is closed somewhere else, so the view's line runs
    through the inside of our face; drawn onto it, it would cut one face into two (eval 181: three
    cylinders, 7 faces, we made 15 of them). Where the part really has two half faces there are two
    such lines around the axis, and then all but one of them are drawn: our own seam stands in for the
    one nearest to it."""
    centre_mm = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    angles: dict[int, list] = {}
    for view, pieces in found.items():
        here, drawing = observed[view], drawings[view]
        for piece in pieces:
            if piece.kind != "line" or piece.verdict == "below" or piece.face >= len(faces):
                continue
            surface = BRepAdaptor_Surface(faces[piece.face])
            if surface.GetType() not in REVOLVED:
                continue
            if surface.LastUParameter() - surface.FirstUParameter() < 2 * np.pi - 0.01:
                continue
            depth, inside = lines._at(drawing.depth, piece.points)
            usable = inside & np.isfinite(depth)
            if usable.sum() < 3:
                continue
            points = lines.lift(view, piece.points[usable], depth[usable], dims, here.mm_per_px, here.mask.shape[0]) + centre_mm
            kind = surface.GetType()
            frame = (surface.Cylinder() if kind == GeomAbs_Cylinder else surface.Cone() if kind == GeomAbs_Cone
                     else surface.Torus() if kind == GeomAbs_Torus else surface.Sphere() if kind == GeomAbs_Sphere else None)
            if frame is None:
                continue
            frame = frame.Position()   # angle 0 around its axis is where our own face is closed
            origin = np.array([frame.Location().X(), frame.Location().Y(), frame.Location().Z()])
            first = np.array([frame.XDirection().X(), frame.XDirection().Y(), frame.XDirection().Z()])
            second = np.array([frame.YDirection().X(), frame.YDirection().Y(), frame.YDirection().Z()])
            around = np.arctan2((points - origin) @ second, (points - origin) @ first)
            mean = np.arctan2(np.sin(around).mean(), np.cos(around).mean())
            spread = np.abs(np.angle(np.exp(1j * (around - mean))))
            if np.degrees(spread.max()) <= MERIDIAN_DEG:
                angles.setdefault(piece.face, []).append((mean, id(piece)))
    found_seams = set()
    for members in angles.values():
        # the lines of one face, grouped by their angle around the axis
        groups: list[list] = []
        for angle, identity in sorted(members):
            if groups and np.degrees(abs(np.angle(np.exp(1j * (angle - groups[-1][-1][0]))))) <= SAME_SEAM_DEG:
                groups[-1].append((angle, identity))
            else:
                groups.append([(angle, identity)])
        if len(groups) > 1 and np.degrees(abs(np.angle(np.exp(1j * (groups[0][0][0] - groups[-1][-1][0]))))) <= SAME_SEAM_DEG:
            groups[0] += groups.pop()   # the same line on both sides of +-180 degrees
        # One line is the seam. Several cut the face into as many faces; ours is closed along a line
        # already, so the one nearest to that is left out and the count comes out right.
        nearest = min(groups, key=lambda group: min(abs(np.angle(np.exp(1j * angle))) for angle, _ in group))
        found_seams.update(identity for _, identity in nearest)
    return found_seams


def choose(found: dict[str, list[lines.Piece]], observed: dict, drawings, dims: dict, limit: int,
           skip: set[int] = frozenset()) -> list[list[tuple]]:
    """The figures to draw, the longest first, each once. A figure is made of every piece of one face
    in one view that is not under our surface; it is drawn when the isometric view confirms most of
    its length to lie on our surface. The pieces it cannot see, and the short ones, only close it."""
    candidates = []
    for view, pieces in found.items():
        groups: dict[int, list] = {}
        for piece in pieces:
            if piece.verdict != "below" and id(piece) not in skip:
                groups.setdefault(piece.face, []).append(piece)
        for members in groups.values():
            for figure in figures(members):
                length = sum(piece.length for piece, _, _ in figure)
                sure = sum(piece.length for piece, _, _ in figure if piece.verdict in ON_SURFACE)
                if length >= MIN_LENGTH_PX and sure >= MIN_CONFIRMED * length:
                    candidates.append((length, figure))
    candidates.sort(key=lambda item: -item[0])
    chosen, taken = [], np.zeros((0, 3))
    for _, figure in candidates:
        if len(chosen) >= limit:
            break
        view = observed[figure[0][0].view]
        pixels = np.vstack([piece.points for piece, _, _ in figure])
        depth, inside = lines._at(drawings[view.name].depth, pixels)
        usable = inside & np.isfinite(depth)
        if usable.sum() < 2:
            continue
        points = lines.lift(view.name, pixels[usable], depth[usable], dims, view.mm_per_px, view.mask.shape[0])
        if len(taken):
            nearest = np.linalg.norm(points[:, None, :] - taken[None, :, :], axis=2).min(axis=1)
            if np.median(nearest) <= SAME_PX * view.mm_per_px:
                continue
        chosen.append(figure)
        taken = np.vstack([taken, points[:: max(1, len(points) // 60)]])
    return chosen


def picture(observed: verify.Observed, drawing: verify.Drawing, pieces: list[lines.Piece], drawn: list[lines.Piece]) -> bytes:
    """PNG of a view: our face boundaries grey, the lines drawn onto the faces green, the lines that
    lie under our surface violet, the rest blue."""
    image = observed.image.copy()
    image[drawing.edges] = (190, 190, 190)
    chosen = {id(p) for p in drawn}
    for piece in pieces:
        colour = (0, 190, 0) if id(piece) in chosen else (255, 0, 200) if piece.verdict == "below" else (255, 130, 0)
        cv2.polylines(image, [np.rint(piece.points).astype(np.int32)], False, colour, 3 if id(piece) in chosen else 1)
    return cv2.imencode(".png", image)[1].tobytes()


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step."""
    with trace.step("split_faces", kind="code",
                    description="Splits a face where the views draw a line across it") as step:
        if case_dir is None:
            return solid
        facts = json.loads((case_dir / "facts.json").read_text())
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        # The pockets step, when it cut nothing, hands on the merged solid with its lines already found
        cached, agent.lines_found = getattr(agent, "lines_found", None), None
        if cached is not None and cached[0] is solid:
            base, observed, drawings, found = cached
        else:
            base = vh.tidy(solid)
            observed = verify.observe(case_dir, dims)
            if not any(name in observed for name in lines.ISOMETRIC):
                step.log("no isometric view with a known scale; no line can be confirmed")
                return solid
            drawings = shading.Drawings(base, observed, dims)
            found = lines.find(observed, drawings, dims)
        count, wanted = len(faces_of(base)), facts.get("faces")
        step.metric(faces=count, **({"faces_of_part": wanted} if wanted else {}))
        every = [p for group in found.values() for p in group]
        step.metric(lines_not_reproduced=len(every),
                    **{verdict: sum(1 for p in every if p.verdict == verdict) for verdict in ("level", "step", "below", "unseen")})
        step.attach("lines.json", [p.summary() for p in every])
        if agent.out_of_time():
            step.log("the case has used its share of the run's time; no face is split")
            return solid
        seam = seams(found, observed, drawings, dims, faces_of(base))
        step.metric(seams=len(seam))
        chosen = choose(found, observed, drawings, dims, MAX_PIECES, seam)
        if not chosen:
            step.log("no line that lies on our surface and is not an edge yet")
            return solid
        jobs = [job for job in (job_for(f, observed[f[0][0].view], drawings[f[0][0].view], dims) for f in chosen)
                if job is not None]
        drawn = [piece for figure in chosen for piece, _, _ in figure]
        step.metric(figures=len(jobs))
        try:
            result = agent.cutter.run(imprint, base, jobs, TIMEOUT)
        except Exception as error:
            step.fail(f"drawing the lines raised {error!r}; keeping the solid as it was")
            return solid
        if result is None:
            step.log("no line could be drawn onto its face")
            return solid
        after = len(faces_of(result))
        volume, before = vh.volume(result), vh.volume(base)
        if after <= count or abs(volume - before) > 1e-6 * before:
            step.log(f"the split changed nothing or moved material ({count} to {after} faces); not kept")
            return solid
        rating = verify.check(base, case_dir, observed).score
        rated = verify.check(result, case_dir, observed).score
        if rated < rating - SCORE_SLACK:
            step.log(f"the verifier rates the split solid lower ({rating:.4f} to {rated:.4f}); not kept", level="warning")
            return solid
        step.metric(lines_drawn=len(drawn), faces_after=after, score_before=round(rating, 4), score_after=round(rated, 4))
        for view in sorted({p.view for p in drawn}):
            step.attach(f"lines_{view}.png", picture(observed[view], drawings[view], found[view], drawn))
        agent.keep_faces = True
        agent._record(step, result, "split")
        return result
