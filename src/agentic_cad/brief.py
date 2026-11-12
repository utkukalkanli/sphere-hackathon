"""Read the brief (description.md) again before the solid is final, and make the solid agree with
what it says about the part's symmetry.

The briefs state symmetries that no single view proves: "symmetric about the vertical plane X = 9.5",
"mirror-symmetric about the central Y-Z plane (X = 40)", "a body of revolution about the Y axis",
"four-fold symmetric about Z". If the part has such a symmetry and our solid contains the part, the
part also lies inside the mirrored (or rotated) copy of our solid, so it lies inside the common
part of the two. Material of ours that has no partner on the other side is excess: intersecting
cuts it away and never touches the part.

Only plain statements are taken. A clause that is negated ("not symmetric", "no plane or rotational
symmetry") or hedged ("roughly", "for the main circular features", "in its outer profile", "the
overall envelope") is ignored, and so is a claim whose plane or axis is not the middle of the
bounding box (a symmetric part's box is symmetric about the same plane). Every intersection is kept
only if the solid keeps the part's volume and the verifier does not rate it lower.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_Transform
from OCP.STEPControl import STEPControl_Reader
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import trace, verify
from agentic_cad import visual_hull as vh
from agentic_cad.export import file_valid

AXES = "xyz"
MIN_SHARE = 0.99        # the result must keep this share of the true volume (a correct one keeps all of it)
MIN_REMOVED = 0.002     # an intersection that removes less than this share of the true volume is not kept
SCORE_SLACK = 0.003     # the verifier's score may drop by this much and the result still pass
CENTRE_TOLERANCE = 0.01  # a stated plane or axis this close (share of the box) to the box's middle is taken
REVOLUTION_FOLDS = 16   # a body of revolution is cut as a 16-fold symmetric one
TIMEOUT = float(os.environ.get("HULL_BRIEF_TIMEOUT", 30))
BUDGET = float(os.environ.get("HULL_BRIEF_BUDGET", 60))   # seconds per case for the whole step

NEGATION = re.compile(r"\b(not|no|non|never|neither|nor|without|asymmetric|unsymmetric)\b")
HEDGE = re.compile(r"\b(rough|roughly|approximately|approx|nearly|almost|mostly|largely|partly|partially|"
                   r"essentially|broadly|like|except|apart from|other than|for the|for its|in its outer|"
                   r"outer profile|envelope|silhouette|overall shape|in plan)\b")
SUBJECT = re.compile(r"\b(part|body|block|whole|it|solid|pattern|piece|component)\b")
SYMMETRY = re.compile(r"symmetr|mirror")
REVOLUTION = re.compile(r"revol(?:ution|ved)|axisymmetr|axially symmetric|rotationally symmetric|"
                        r"cylindrically symmetric|turned part|lathe")
FOLD_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "twelve": 12}
FOLD = re.compile(r"\b(\d+|two|three|four|five|six|eight|twelve)[- ]?fold\b")


@dataclass(frozen=True)
class Claim:
    kind: str          # "mirror" (a plane square to `axis`) or "turn" (rotations about `axis`)
    axis: str          # "x", "y" or "z"
    folds: int = 0     # for "turn": the number of rotations that map the part onto itself
    text: str = ""

    def summary(self) -> str:
        if self.kind == "mirror":
            return f"mirror symmetric about the middle plane square to {self.axis.upper()}"
        if self.folds >= REVOLUTION_FOLDS:
            return f"a body of revolution about {self.axis.upper()}"
        return f"{self.folds}-fold symmetric about {self.axis.upper()}"


def _plain(text: str) -> str:
    """Unicode hyphens and dashes to '-', emphasis marks dropped, all lower case."""
    for dash in "‐‑‒–—−":
        text = text.replace(dash, "-")
    return text.replace("**", "").replace("__", "").lower()


def clauses(text: str) -> list[str]:
    """The brief cut into clauses: sentences, then ';', ', so', ' but', ' while', ' whereas'."""
    parts = []
    for line in _plain(text).splitlines():   # headings and list items are separate lines
        line = re.sub(r"\s+", " ", line).strip(" #-*")
        parts += re.split(r"(?<=[a-z)\]%])\.\s+|;\s*|,?\s+but\s+|,?\s+while\s+|,?\s+whereas\s+|,\s+so\s+|:\s+", line)
    return [part.strip(" .") for part in parts if part.strip(" .")]


def _middle(axis: str, value: float | None, dims: dict[str, float]) -> bool:
    """A stated plane or axis position that is (or is taken as) the middle of the box."""
    return value is None or abs(value - dims[axis] / 2) <= CENTRE_TOLERANCE * dims[axis] + 0.05


def _mirror_axes(clause: str, dims: dict[str, float]) -> list[str]:
    axes: list[str] = []

    def add(axis: str, value: float | None) -> None:
        if axis in AXES and _middle(axis, value, dims) and axis not in axes:
            axes.append(axis)

    for match in re.finditer(r"\b([xyz])\s*=\s*(\d+(?:\.\d+)?)", clause):
        add(match.group(1), float(match.group(2)))
    stated = {m.group(1) for m in re.finditer(r"\b([xyz])\s*=\s*\d", clause)}
    for match in re.finditer(r"\b([xyz])\s*-\s*([xyz])\s+(?:mid-?)?plane", clause):
        normal = (set(AXES) - {match.group(1), match.group(2)})
        if len(normal) == 1 and not stated & normal:
            add(normal.pop(), None)
    for match in re.finditer(r"\bmid-?([xyz])\b", clause):
        if match.group(1) not in stated:
            add(match.group(1), None)
    for match in re.finditer(r"(?:symmetric|mirror[- ]symmetric|mirrored)\s+(?:[a-z-]+\s+){0,2}in\s+([xyz])\b", clause):
        add(match.group(1), None)
    return axes


def _turn_axis(clause: str) -> str | None:
    for pattern in (r"about (?:the |its )?([xyz])[- ]?axis", r"about (?:the |its )?(?:axis )?([xyz])\b",
                    r"axis (?:runs |lies |is )?(?:along|parallel to) (?:the )?([xyz])\b",
                    r"around (?:the |its )?([xyz])[- ]?axis"):
        match = re.search(pattern, clause)
        if match:
            return match.group(1)
    return None


def parse(text: str, dims: dict[str, float]) -> list[Claim]:
    """The symmetries the brief states plainly about the whole part."""
    claims: list[Claim] = []
    for clause in clauses(text):
        mirror, turn = bool(SYMMETRY.search(clause)), bool(REVOLUTION.search(clause)) or bool(FOLD.search(clause))
        if not (mirror or turn) or NEGATION.search(clause) or HEDGE.search(clause) or not SUBJECT.search(clause):
            continue
        fold = FOLD.search(clause)
        if turn:
            axis = _turn_axis(clause)
            if axis is None:
                continue
            others = [dims[a] for a in AXES if a != axis]
            if fold:
                word = fold.group(1)
                folds = int(word) if word.isdigit() else FOLD_WORDS[word]
            elif REVOLUTION.search(clause):
                folds = REVOLUTION_FOLDS
            else:
                continue
            # rotating by a quarter turn or less swaps the two other sizes of the box: they must agree
            if folds >= 3 and abs(others[0] - others[1]) > CENTRE_TOLERANCE * max(others) + 0.05:
                continue
            if folds >= 2:
                claims.append(Claim("turn", axis, folds, clause))
            continue
        if re.search(r"mirror images? of one another|mirror images? of each other", clause):
            continue   # two features mirror each other; whether the whole part does is said elsewhere
        for axis in _mirror_axes(clause, dims):
            claims.append(Claim("mirror", axis, 0, clause))
    unique: dict[tuple, Claim] = {}
    for claim in claims:
        key = (claim.kind, claim.axis)
        if key not in unique or claim.folds > unique[key].folds:
            unique[key] = claim
    return list(unique.values())


def read(case_dir: Path | None, dims: dict[str, float]) -> list[Claim]:
    path = case_dir / "description.md" if case_dir is not None else None
    if path is None or not path.is_file():
        return []
    return parse(path.read_text(encoding="utf-8", errors="replace"), dims)


# -- the geometry, run in the cutter's child process ---------------------------------------------

def _direction(axis: str) -> gp_Dir:
    return gp_Dir(*(1.0 if a == axis else 0.0 for a in AXES))


def _common(shape: TopoDS_Shape, trsf: gp_Trsf) -> TopoDS_Shape | None:
    copy = BRepBuilderAPI_Transform(shape, trsf, True).Shape()
    common = BRepAlgoAPI_Common(shape, copy)
    common.Build()
    if not common.IsDone():
        return None
    return common.Shape()   # Cutter.run keeps the largest solid of the result


def intersect(shape: TopoDS_Shape, argument: tuple) -> TopoDS_Shape | None:
    """shape and its copies under the claim, in common. argument: (kind, axis, folds, centre xyz)."""
    kind, axis, folds, centre = argument
    middle = gp_Pnt(*centre)
    trsf = gp_Trsf()
    if kind == "mirror":
        trsf.SetMirror(gp_Ax2(middle, _direction(axis)))
        return _common(shape, trsf)
    line = gp_Ax1(middle, _direction(axis))
    if folds & (folds - 1) == 0:   # a power of two: halve the angle each time, each result keeps the earlier symmetry
        angle, result = math.pi, shape
        while result is not None and angle >= 2 * math.pi / folds - 1e-9:
            trsf.SetRotation(line, angle)
            result = _common(result, trsf)
            angle /= 2
        return result
    result = shape
    for k in range(1, folds):
        if result is None:
            break
        trsf = gp_Trsf()
        trsf.SetRotation(line, 2 * math.pi * k / folds)
        result = _common(result, trsf)
    return result


# -- the pipeline step ---------------------------------------------------------------------------

def as_filed(solid: TopoDS_Shape) -> TopoDS_Shape | None:
    """The solid as its .stp reads back (faces merged, tolerances of the file). A boolean that fails
    on the solid in memory can succeed on this one (practice 8: the mirror common failed in the
    pipeline and worked on the same solid read from its stage file)."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "filed.stp"
            vh.write_step(vh.tidy(BRepBuilderAPI_Copy(solid, True, False).Shape()), path)
            if not file_valid(path):
                return None
            reader = STEPControl_Reader()
            reader.ReadFile(str(path))
            reader.TransferRoots()
            return reader.OneShape()
    except Exception:
        return None


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path],
          name: str = "check_brief") -> TopoDS_Shape:
    with trace.step(name, kind="code",
                    description="Reads the brief again and cuts what its stated symmetries rule out") as step:
        if case_dir is None:
            return solid
        started = time.time()
        dims = dict(zip(AXES, vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        claims = read(case_dir, dims)
        step.attach("brief_claims.json", [{"claim": c.summary(), "from": c.text} for c in claims])
        if not claims:
            step.log("the brief states no symmetry plainly")
            return solid
        centre = tuple(dims[a] / 2 for a in AXES)
        observed = verify.observe(case_dir, dims)
        score = before = verify.check(solid, case_dir, observed).score
        current, results = solid, []
        for claim in claims:
            if agent.out_of_time() or time.time() - started > BUDGET:
                results.append({"claim": claim.summary(), "result": "no time left"})
                continue
            argument = (claim.kind, claim.axis, claim.folds, centre)
            carved = None
            for base in (current, "filed"):
                if base == "filed":
                    base = as_filed(current)
                    if base is None or time.time() - started > BUDGET:
                        break
                    step.log(f"{claim.summary()}: no result on the solid in memory; trying it as read from its file")
                carved = agent._guarded(step, base, target,
                                        lambda: agent.cutter.run(intersect, base, argument, TIMEOUT),
                                        quiet=True, share=MIN_SHARE)
                if carved is not None:
                    break
            if carved is None:
                results.append({"claim": claim.summary(), "result": "refused (failed, timed out or below the true volume)"})
                continue
            removed = vh.volume(current) - vh.volume(carved)
            if target and removed < MIN_REMOVED * target:
                results.append({"claim": claim.summary(), "result": "the solid already agrees",
                                "removed_mm3": round(removed, 1)})
                continue
            after = verify.check(carved, case_dir, observed).score
            if after < score - SCORE_SLACK:
                step.log(f"{claim.summary()}: the verifier rates the result lower ({score:.3f} to {after:.3f}); not kept",
                         level="warning")
                results.append({"claim": claim.summary(), "result": "verifier lower", "removed_mm3": round(removed, 1),
                                "score": [round(score, 4), round(after, 4)]})
                continue
            step.log(f"{claim.summary()}: removed {removed / 1000:.2f} cm3 our solid had on one side only")
            results.append({"claim": claim.summary(), "result": "kept", "removed_mm3": round(removed, 1),
                            "score": [round(score, 4), round(after, 4)]})
            current, score = carved, after
        step.attach("brief_checks.json", results)
        step.metric(claims=len(claims), kept=sum(r["result"] == "kept" for r in results),
                    score_before=round(before, 4), score_after=round(score, 4))
        if current is solid:
            return solid
        agent._record(step, current, "brief" if name == "check_brief" else "brief_again")
        return current


def _final(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    return apply(agent, solid, case_dir, views, name="recheck_brief")


# The same check once more, as the last step: the steps after the first one cut one side of a
# symmetric part more than the other (practice 8: 0.47 % of the true volume left on one side only)
final = SimpleNamespace(apply=_final)
