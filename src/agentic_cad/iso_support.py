"""Which of the material that the isometric silhouette would remove really goes.

The isometric views are not carved with on their own (decisions.md). hull_agent carves the box a
second time with the isometric silhouette first; what that removes compared with the hull of the
three straight cuts is judged here against the other data of the case:

- as a whole: if the solid without it agrees with the lines and silhouettes of all 8 views at least
  as well as the hull did (verify.py), all of it goes;
- otherwise piece by piece: the removed material is split into its connected pieces, and the larger
  ones are taken away one after the other, each only if the views do not agree worse without it.
  A piece the views speak against stays (the isometric cut shaves a slanted face off a feature it
  only half sees: that face shows up as an edge where the views have no line).

The volume is left out of the score on purpose: every removal brings an oversized solid closer to
the true volume, which would vote for all of them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from agentic_cad import verify
from agentic_cad import visual_hull as vh

SCORE_SLACK = 0.003     # the views may agree this much worse and the removal still passes
MAX_PIECES = 8          # pieces judged one by one, largest first
MIN_PIECE_SHARE = 0.005  # ... each at least this share of the true volume
TIMEOUT = 20.0


def views_score(shape: TopoDS_Shape, case_dir: Path, observed: dict) -> float:
    """How well the solid's edges and outline agree with the 8 views; the volume plays no part."""
    report = verify.check(shape, case_dir, observed)
    return report.mean("lines") * report.mean("silhouette")


def difference(shape: TopoDS_Shape, other_path: str) -> TopoDS_Shape | None:
    """`shape` without the solid stored at `other_path`: possibly several pieces. Runs in the child."""
    cut = BRepAlgoAPI_Cut(shape, vh.read_brep(other_path))
    return cut.Shape() if cut.IsDone() else None


def without(shape: TopoDS_Shape, piece_path: str) -> TopoDS_Shape | None:
    """`shape` with one piece taken away, as one valid solid. Runs in the child."""
    result = difference(shape, piece_path)
    solid = vh.largest_solid(result) if result is not None else None
    if solid is None or vh.volume(solid) <= 0 or not BRepCheck_Analyzer(solid).IsValid():
        return None
    return solid


def _solids(shape: TopoDS_Shape) -> list[TopoDS_Shape]:
    found, explorer = [], TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        found.append(TopoDS.Solid(explorer.Current()))
        explorer.Next()
    return sorted(found, key=vh.volume, reverse=True)


def choose(agent, step, hull: TopoDS_Shape, carved: TopoDS_Shape, case_dir: Path, dims: dict,
           target: float) -> TopoDS_Shape | None:
    """The hull with as much of (hull - carved) removed as the views support, or None for nothing."""
    observed = agent.evidence(case_dir, dims).observed
    base = views_score(hull, case_dir, observed)
    whole = views_score(carved, case_dir, observed)
    step.metric(views_before=round(base, 4), views_all_removed=round(whole, 4))
    if whole >= base - SCORE_SLACK:
        step.log(f"the views agree as well without all of it ({base:.3f} to {whole:.3f}): all removed")
        return carved
    step.log(f"the views agree worse without all of it ({base:.3f} to {whole:.3f}): judging its pieces")
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/carved.brep"
        BRepTools.Write_s(carved, path)
        removed = agent.cutter.run(difference, hull, path, TIMEOUT, whole=True)
        if removed is None:
            return None
        pieces = [p for p in _solids(removed) if vh.volume(p) >= MIN_PIECE_SHARE * target][:MAX_PIECES]
        step.metric(pieces=len(pieces))
        current, score, taken = hull, base, 0
        for index, piece in enumerate(pieces):
            if agent.out_of_time():
                break
            piece_path = f"{tmp}/piece_{index}.brep"
            BRepTools.Write_s(piece, piece_path)
            try:
                candidate = agent.cutter.run(without, current, piece_path, TIMEOUT)
            except TimeoutError:
                candidate = None
            if candidate is None or vh.volume(candidate) < 0.97 * target:
                continue
            after = views_score(candidate, case_dir, observed)
            share = vh.volume(piece) / target
            if after >= score - SCORE_SLACK:
                current, score, taken = candidate, after, taken + 1
                step.log(f"piece {index} ({share:.3f} of the true volume) removed: views {after:.3f}")
            else:
                step.log(f"piece {index} ({share:.3f} of the true volume) stays: views would go to {after:.3f}")
        step.metric(pieces_removed=taken)
        return current if taken else None
