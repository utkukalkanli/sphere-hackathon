"""One rule for accepting a cut: does the solid, where the cut changed it, agree with the views at
least as well as before?

The verifier's score (verify.check) is one number for the whole part, multiplied by the volume
ratio, and lets most wrong cuts through (docs/memory/knowledge.md, "The accept rule of the later
steps"). This rule compares the candidate with the solid it was cut from pixel by pixel in the
eight views. Four errors per view:

- tone: |tone of the view - tone the renderer gives the candidate's normal there| (tone.py), clipped
  at TONE_CLIP levels and counted in units of it, away from lines and edges;
- mask: the silhouettes differ;
- shp:  a sharp edge of the candidate with no line of the view within the tolerance;
- rec:  a line of the view with no face boundary of the candidate within the tolerance.

Pixels the cut does not reach have the same errors before and after, so the change of each sum is
the judgement of the cut where it acts. A cut is accepted when the candidate is one valid solid,
keeps the part's volume (MIN_VOLUME_SHARE of facts.json), tone + mask + shp does not rise, and mask
alone does not rise (a large gain in tone must not buy a broken outline). rec is reported and not
used: a line that the candidate still lacks says nothing about which side of it has to move.

Whether the whole part's volume comes closer to the true one is reported and not asked: a right cut
can move the total away from it when an error elsewhere was cancelling the excess
(docs/memory/decisions.md, "A change is judged where it acts").

HULL_JUDGE=local makes shading.py, pockets.py and the column cuts of depth.py use this rule;
the default, "score", is the verifier's score as before. Measured on 23 eval parts
(tools/check_judge.py, 2026-11-12) the default stays: of the cuts these steps propose the score
rule kept no wrong one, and this rule refused 15 of 27 right ones. A right cut whose neighbour is
still uncut gets walls that the part does not have, and their tones count against it (eval 236).
A variant that also asked the tone error to fall by 0.02 or 0.05 per changed pixel only lost more
right cuts and is not kept.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import tone
from agentic_cad import verify
from agentic_cad import visual_hull as vh

RULE = os.environ.get("HULL_JUDGE", "score")
TONE_CLIP = 30.0          # grey levels; a pixel further off than this counts as one whole wrong pixel
MIN_VOLUME_SHARE = 0.97   # of the true volume, as everywhere: less means real material was cut
SLACK = 1e-6              # of a pixel: "does not rise" must survive rounding


def local() -> bool:
    return RULE == "local"


@dataclass
class Verdict:
    accepted: bool
    reason: str               # why not, or "" when accepted
    tone: float = 0.0         # change of each error summed over the views, in pixels; below 0 is better
    mask: float = 0.0
    shp: float = 0.0
    rec: float = 0.0
    changed: int = 0          # pixels of the views where the cut moved the surface or the outline
    volume_before: float | None = None   # solid / true volume
    volume_after: float | None = None

    @property
    def error(self) -> float:
        """The sum the rule asks about."""
        return self.tone + self.mask + self.shp

    def summary(self) -> dict:
        return {key: (round(value, 3) if isinstance(value, float) else value) for key, value in asdict(self).items()}

    def __str__(self) -> str:
        return (f"tone {self.tone:+.0f}, outline {self.mask:+.0f}, edges without a line {self.shp:+.0f}, "
                f"lines without an edge {self.rec:+.0f} px over {self.changed} changed px")


def note(agent, step: str, base: TopoDS_Shape, candidate: TopoDS_Shape, accepted: bool, verdict: Verdict | None = None) -> None:
    """Remember a decision on `agent.decisions`, if a tool has put a list there (tools/check_judge.py)."""
    log = getattr(agent, "decisions", None)
    if log is not None:
        log.append({"step": step, "base": base, "candidate": candidate, "accepted": accepted, "verdict": verdict})


class Judge:
    """Rates candidates of one case. The error maps of the last solids drawn are kept, so a base that
    is rated against several candidates, or an accepted candidate that becomes the base, is drawn once."""

    def __init__(self, case_dir: Path, dims: dict[str, float], observed: dict[str, verify.Observed],
                 table: tone.Table | None = None):
        self.case_dir, self.dims, self.observed, self.table = case_dir, dims, observed, table
        self.target = vh.read_volume(case_dir)
        self._deflection = 0.3 * min(view.mm_per_px for view in observed.values()) if observed else 1.0
        self._distance = {name: cv2.distanceTransform((~view.lines).astype(np.uint8), cv2.DIST_L2, 3)
                          for name, view in observed.items()}
        self._tones = {name: view.image.max(axis=2).astype(np.float32) for name, view in observed.items()}
        self._kept: list[tuple[TopoDS_Shape, dict]] = []   # the last few solids and their maps

    def maps(self, solid: TopoDS_Shape) -> dict[str, dict]:
        """Per view, the four error maps of `solid` and its depth."""
        for shape, known in self._kept:
            if shape is solid:
                return known
        mesh = verify.tessellate(solid, self._deflection)
        drawings = {name: verify.draw(mesh, name, self.dims, view.mm_per_px, view.mask.shape[0])
                    for name, view in self.observed.items()}
        if self.table is None:  # which renderer made the views: asked once, of the first solid
            self.table = tone.choose(self.observed, drawings)
        result = {}
        for name, view in self.observed.items():
            drawing = drawings[name]
            mask = view.mask ^ drawing.mask
            if self.table is not None:
                expected = self.table.tone(tone.in_camera(drawing.normal, name))
                blocked = cv2.dilate((view.lines | drawing.edges).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
                off = np.clip(np.abs(self._tones[name] - expected) / TONE_CLIP, 0, 1)
                tones = np.where(view.mask & drawing.mask & ~blocked, off, 0).astype(np.float32)
            else:
                tones = np.zeros(mask.shape, np.float32)
            from_edges = cv2.distanceTransform((~drawing.edges).astype(np.uint8), cv2.DIST_L2, 3)
            result[name] = {
                "tone": tones, "mask": mask,
                "rec": view.lines & drawing.mask & (from_edges > verify.TOLERANCE_PX) & ~mask,
                "shp": drawing.sharp & view.mask & (self._distance[name] > verify.TOLERANCE_PX) & ~mask,
                "depth": drawing.depth, "drawn": drawing.mask, "mm_per_px": view.mm_per_px,
            }
        self._kept = [*self._kept[-2:], (solid, result)]
        return result

    def rate(self, base: TopoDS_Shape, candidate: TopoDS_Shape) -> Verdict:
        """The candidate against the solid it was made from."""
        before, after = vh.volume(base), vh.volume(candidate)
        ratios = {"volume_before": before / self.target, "volume_after": after / self.target} if self.target else {}
        if not BRepCheck_Analyzer(candidate).IsValid():
            return Verdict(False, "not a valid solid", **ratios)
        if self.target and after < MIN_VOLUME_SHARE * self.target:
            return Verdict(False, "less than the part's volume would be left", **ratios)
        old, new = self.maps(base), self.maps(candidate)
        change = {"tone": 0.0, "mask": 0.0, "shp": 0.0, "rec": 0.0}
        changed = 0
        for name in old:
            b, a = old[name], new[name]
            for key in change:
                change[key] += float(a[key].sum(dtype=np.float64)) - float(b[key].sum(dtype=np.float64))
            with np.errstate(invalid="ignore"):
                moved = np.abs(np.where(a["drawn"] & b["drawn"], a["depth"] - b["depth"], 0.0)) > 0.5 * b["mm_per_px"]
            changed += int(((a["drawn"] != b["drawn"]) | moved).sum())
        verdict = Verdict(True, "", changed=changed, **change, **ratios)
        if verdict.mask > SLACK:
            verdict.accepted, verdict.reason = False, "the outline fits the views worse"
        elif verdict.error > SLACK:
            verdict.accepted, verdict.reason = False, "the views fit worse where the cut is"
        return verdict
