"""Depth from shading, deterministic: how deep the flat faces lie that no silhouette shows.

The renders shade every face by the direction it faces. One tone is one direction, not one depth:
in a straight view every flat face square to the view has the same tone, however deep it lies. But
the isometric views see three sides of the part at once and give each its own tone, and a face that
lies deeper appears shifted in them. So the depth of a flat face is found by matching:

1. In a straight view, the areas with the tone of a face square to the view, enclosed by dark
   lines, are the flat faces looking at that camera ("regions").
2. A region is slid down along the view direction. At every depth it is projected into the
   isometric view that sees that side of the part. At the true depth the pixels it lands on show
   the tone of that side, and its outline lands on dark lines.
3. Where such a depth is found below the surface of the solid, the column above the region is cut
   away down to it.

Slanted flat faces are found the same way. In a straight view they are the areas of one even tone
that is not the tone of a face square to the view. A slanted plane meets a face square to the view
in a level line, which is a straight edge of the area; so the plane is hung on such an edge at the
depth the neighbouring face has in the solid, and only its slope is tried. It is kept where its
outline lands on lines of the isometric view and its pixels there have one tone.

Only points that are certainly visible from the isometric camera are compared: those whose line of
sight leaves the solid through the cut column itself or above everything else. A cut is kept only
if it leaves at least the part's true volume and the verifier does not rate the result lower.

The tones are not assumed: for every view they are measured on the faces of the solid so far, so
the step does not depend on the renderer's lighting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape

from agentic_cad import trace
from agentic_cad import verify
from agentic_cad import visual_hull as vh
from agentic_cad import judge

TONE_TOLERANCE = (2, 10)  # tones this close (0..255) are one face direction: measured per view, within these limits
MIN_REGION_PX = 250      # smaller regions are not worth a cut
MIN_ENCLOSED = 0.8       # share of a region's border that must be a dark line or the background
MIN_VISIBLE = 30         # certainly visible points needed inside a region to judge a depth
MIN_VISIBLE_BORDER = 15  # ... and on its border
MIN_VISIBLE_SHARE = 0.08  # ... and both as a share of the points tried
MIN_SHARE = 0.97         # as in hull_agent: no cut may leave less than this share of the true volume
MIN_EXCESS = 1.02        # the step is skipped when the solid has less than this times the true volume
MIN_TONE_MATCH = 0.9     # share of the visible points that must show the expected tone
MIN_LINE_MATCH = 0.7     # share of the visible border points that must lie on a line
MIN_GAIN = 0.12          # the best depth must match this much better than the present surface
MIN_DROP_PX = 4.0        # a cut shallower than this many pixels is not made
LINE_NEAR_PX = 2.5
MAX_DEPTHS = 240         # depths tried per region
MAX_REGIONS = 14         # regions tried per view, largest first
SLOPES_DEG = np.arange(6.0, 82.1, 2.0)   # slopes tried for a slanted face, from the plane square to the view
MIN_LINE_MATCH_SLANTED = 0.8
MAX_SLANTED = 8          # slanted regions tried per view, largest first
MIN_SUPPORT = 0.3        # a deep floor: at least this share of the region must show the tone at its depth
SURE_GAIN = 0.3          # ... a floor nothing can check: this much more of it must show the tone than at the surface
DEEP_MIN_AREA_PX = 10000  # regions smaller than this are not searched by tone alone
DEEP_MAX_DROP = 2.0      # ... nor deeper than this many times their width (the square root of their area)
PLATEAU = 0.03           # depths matching this close to the best count as equally good: the shallowest is taken
DEEP = os.environ.get("HULL_SHADING_DEEP", "1") != "0"         # also look for floors by tone alone
MAX_PASSES = 3           # a cut can uncover the next face behind it: the views are gone through again
SCORE_SLACK = 0.004      # how much lower the verifier may rate a cut that is still kept
SLANTED = os.environ.get("HULL_SHADING_SLANTED", "1") != "0"   # also look for slanted flat faces
TIMEOUT = float(os.environ.get("HULL_SHADING_TIMEOUT", 30))
# No clock of its own: the work is bounded by what is counted (MAX_PASSES, MAX_REGIONS, MAX_SLANTED and
# the six cuts tried singly), so a slower machine gives the same solid. Only the case's share of the
# run's time (agent.out_of_time) can stop the step.


@dataclass
class Pocket:
    view: str            # the straight view the region was found in
    depth: float         # mm from the box face that view looks at down to the floor
    drop: float          # mm below the present surface of the solid
    area_px: int
    tone_match: float
    line_match: float
    gain: float          # score at this depth minus the score at the present surface
    removes: float = 0.0  # mm3 the cut takes away from the solid as it is
    slope: float = 0.0    # degrees between the face and a plane square to the view; 0 for a flat floor
    region: np.ndarray | None = None          # the pixels of the straight view it was found in
    silhouette: vh.Silhouette | None = None   # the region as an outline, extruded down to the floor

    def summary(self) -> dict:
        return {"view": self.view, "depth_mm": round(self.depth, 3), "drop_mm": round(self.drop, 3),
                "area_px": self.area_px, "tone_match": round(self.tone_match, 3),
                "line_match": round(self.line_match, 3), "gain": round(self.gain, 3),
                "removes_mm3": round(float(self.removes), 1), "slope_deg": round(float(self.slope), 1)}


def tone_of(image: np.ndarray) -> np.ndarray:
    return image.max(axis=2).astype(np.int16)  # the part is tan: its brightest channel carries the shade


def iso_for(view: str) -> str:
    """The isometric view that sees the faces looking at this straight camera."""
    towards = verify.camera(view)[2]
    return max(("iso_front_right_top", "iso_back_left_bottom"), key=lambda iso: verify.camera(iso)[2] @ towards)


def face_tone(observed: verify.Observed, drawing: verify.Drawing, normal: np.ndarray) -> tuple[int, int] | None:
    """The tone this view gives a face with this normal, and how far it strays from it, measured where
    the solid has such a face. A renderer with a light near the camera shades a flat face unevenly."""
    clear = ~cv2.dilate(observed.lines.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    facing = drawing.mask & observed.mask & clear & (drawing.normal @ normal > 0.999)
    if facing.sum() < 150:
        return None
    return _peak(np.bincount(tone_of(observed.image)[facing], minlength=256).astype(float))


def facing_tone(view: str, observed: dict, drawings) -> tuple[int, int] | None:
    """The tone of a face square to the camera, and its spread. The light sits with the camera, so it
    is the same tone in all six straight views: the middle one of the six is used for each. Measured in
    one view alone it can be the tone of a slanted face, where that is most of what the view shows
    (eval part 171, front: 180 for a sloping top, 176 and 177 in the other views)."""
    cached = getattr(drawings, "facing", None)
    if cached is None:
        measured = {}
        for name in verify.ORTHO_VIEWS:
            if name in observed:
                tone = face_tone(observed[name], drawings[name], verify.camera(name)[2])
                if tone is not None:
                    measured[name] = tone
        cached = {}
        if measured:
            middle = int(np.median([tone for tone, _ in measured.values()]))
            agreeing = [spread for tone, spread in measured.values() if abs(tone - middle) <= 3]
            common = (middle, int(np.median(agreeing)) if agreeing else TONE_TOLERANCE[0])
            cached = {name: common for name in measured}
        try:
            drawings.facing = cached
        except AttributeError:
            pass
    return cached.get(view)


def _peak(counts: np.ndarray) -> tuple[int, int]:
    """The commonest tone and the spread of its peak: the narrowest band around it holding 90 % of
    what lies near it."""
    tone = int(np.convolve(counts, np.ones(5), mode="same").argmax())
    near = counts[max(0, tone - TONE_TOLERANCE[1]):tone + TONE_TOLERANCE[1] + 1].sum()
    spread = next(w for w in range(TONE_TOLERANCE[0], TONE_TOLERANCE[1] + 1)
                  if w == TONE_TOLERANCE[1] or counts[max(0, tone - w):tone + w + 1].sum() >= 0.9 * near)
    return tone, spread


def regions(observed: verify.Observed, tone: int, spread: int) -> list[np.ndarray]:
    """The flat faces square to a straight view: areas of the facing tone enclosed by lines."""
    lines = cv2.dilate(observed.lines.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    flat = observed.mask & ~lines & (np.abs(tone_of(observed.image) - tone) <= spread)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(flat.astype(np.uint8), connectivity=4)
    border_ok = lines | ~observed.mask
    found = []
    for label in np.argsort(-stats[1:, cv2.CC_STAT_AREA])[:3 * MAX_REGIONS] + 1:
        if stats[label, cv2.CC_STAT_AREA] < MIN_REGION_PX:
            break
        region = labels == label
        ring = cv2.dilate(region.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~region
        # a band of the facing tone on a curved face fades out instead of ending at a line
        if ring.sum() and (ring & border_ok).sum() / ring.sum() >= MIN_ENCLOSED:
            found.append(region)
        if len(found) == MAX_REGIONS:
            break
    return found


def _pick(mask: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = np.nonzero(mask)
    if len(rows) > limit:
        keep = np.linspace(0, len(rows) - 1, limit).astype(int)
        rows, cols = rows[keep], cols[keep]
    return rows, cols


def border_of(region: np.ndarray, lines: np.ndarray) -> np.ndarray:
    """The outline of a region, on the middle of the lines drawn around it. A region ends where the
    line begins, a few pixels inside the edge of the face; projected into another view that offset
    would miss the line there."""
    rings, grown = [], region.astype(np.uint8)
    for _ in range(6):
        wider = cv2.dilate(grown, np.ones((3, 3), np.uint8))
        rings.append((wider > 0) & (grown == 0))
        grown = wider
    on_line = np.array([(ring & lines).sum() / max(1, ring.sum()) for ring in rings])
    if on_line.max() < 0.3:
        return rings[0]
    # the first run of rings that lie on a line is the region's own line; with thin walls the line on
    # the far side of the wall comes a few rings later
    good = on_line >= 0.85 * on_line.max()
    first = int(np.argmax(good))
    last = first
    while last + 1 < len(rings) and good[last + 1]:
        last += 1
    return rings[(first + last) // 2]


def sweep(region: np.ndarray, view: str, observed: dict, drawings: dict, dims: dict, tone: int, spread: int):
    """Try every depth for one region. Returns (depths, tone match, line match, present surface):
    the depths in mm towards the camera from the centre of the box, NaN where too little is visible."""
    here, iso_name = observed[view], iso_for(view)
    there = observed[iso_name]
    right, up, towards = verify.camera(view)
    iso_right, iso_up, iso_towards = verify.camera(iso_name)
    size = np.array([dims["x"], dims["y"], dims["z"]])
    top = float(abs(towards) @ size) / 2            # the box face the view looks at
    half, iso_half = region.shape[0] / 2, there.mask.shape[0] / 2
    surface = drawings[view].depth

    inner = cv2.erode(region.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    grown = cv2.dilate(region.astype(np.uint8), np.ones((13, 13), np.uint8)).astype(bool)
    border = border_of(region, here.lines)
    rows_in, cols_in = _pick(inner if inner.sum() >= MIN_VISIBLE else region, 640)
    rows_bd, cols_bd = _pick(border, 400)
    rows, cols = np.concatenate([rows_in, rows_bd]), np.concatenate([cols_in, cols_bd])
    is_border = np.arange(len(rows)) >= len(rows_in)

    present = surface[region]
    present = present[np.isfinite(present)]
    if not len(present):
        return None
    start = float(np.percentile(present, 95))      # the present surface over the region
    step = there.mm_per_px / np.sqrt(2 / 3)         # one isometric pixel of shift per step
    lowest = -top + 2 * here.mm_per_px
    count = int(min(MAX_DEPTHS, max(2, (start - lowest) / step + 1)))
    depths = np.linspace(start, lowest, count)

    a = (cols + 0.5 - half) * here.mm_per_px
    b = (half - 0.5 - rows) * here.mm_per_px
    # where each point lands in the isometric view, per depth: (depths, points)
    planar = np.outer(a, right) + np.outer(b, up)
    iso_a = (planar @ iso_right)[None, :] + depths[:, None] * (towards @ iso_right)
    iso_b = (planar @ iso_up)[None, :] + depths[:, None] * (towards @ iso_up)
    iso_cols = np.rint(iso_a / there.mm_per_px + iso_half - 0.5).astype(int)
    iso_rows = np.rint(iso_half - 0.5 - iso_b / there.mm_per_px).astype(int)
    inside = (iso_cols >= 0) & (iso_cols < 2 * iso_half) & (iso_rows >= 0) & (iso_rows < 2 * iso_half)
    iso_cols, iso_rows = np.clip(iso_cols, 0, int(2 * iso_half) - 1), np.clip(iso_rows, 0, int(2 * iso_half) - 1)

    # Certainly visible: walking from the point towards the isometric camera, the line of sight stays
    # above the solid's surface as this view sees it, or inside the column that would be cut away.
    rise = float(iso_towards @ towards)                                   # mm up per mm along the ray
    drift = np.array([iso_towards @ right, iso_towards @ up]) / here.mm_per_px   # pixels sideways per mm
    reach = (top - depths) / rise                                         # ray length to the box face
    steps = int(np.clip(np.abs(drift).max() * reach.max(), 8, 96))
    visible = np.ones((count, len(rows)), bool)
    blocking = np.where(np.isfinite(surface), surface, -np.inf)
    for index in range(1, steps + 1):
        along = reach * index / steps                                     # (depths,)
        c = np.rint(cols[None, :] + along[:, None] * drift[0]).astype(int)
        r = np.rint(rows[None, :] - along[:, None] * drift[1]).astype(int)
        ok = (c >= 0) & (c < region.shape[1]) & (r >= 0) & (r < region.shape[0])
        c, r = np.clip(c, 0, region.shape[1] - 1), np.clip(r, 0, region.shape[0] - 1)
        height = depths[:, None] + along[:, None] * rise
        visible &= ~ok | grown[r, c] | (blocking[r, c] <= height + 0.5 * here.mm_per_px)
    visible &= inside

    iso_tone = tone_of(there.image)
    iso_lines = cv2.dilate(there.lines.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    near_line = cv2.distanceTransform((~there.lines).astype(np.uint8), cv2.DIST_L2, 3) <= LINE_NEAR_PX
    shows = there.mask[iso_rows, iso_cols] & (np.abs(iso_tone[iso_rows, iso_cols] - tone) <= spread)
    on_line = iso_lines[iso_rows, iso_cols]

    judged = visible & ~is_border[None, :] & ~on_line
    seen = judged.sum(axis=1)
    tone_match = np.where(seen >= max(MIN_VISIBLE, MIN_VISIBLE_SHARE * len(rows_in)), (judged & shows).sum(axis=1) / np.maximum(seen, 1), np.nan)
    edge = visible & is_border[None, :]
    seen_edge = edge.sum(axis=1)
    line_match = np.where(seen_edge >= max(MIN_VISIBLE_BORDER, MIN_VISIBLE_SHARE * len(rows_bd)),
                          (edge & near_line[iso_rows, iso_cols]).sum(axis=1) / np.maximum(seen_edge, 1), np.nan)
    return depths, tone_match, line_match, top


def _confirmed_tone(view: str, observed: dict, drawings, dims: dict, facing: tuple[int, int]) -> tuple[int, int] | None:
    """The tone the isometric view shows under faces of the solid that are certainly where the solid
    has them: regions of the straight view whose outline, at the solid's present surface, lands on
    lines of the isometric view and whose pixels there share one tone."""
    there = observed[iso_for(view)]
    tones, counts = tone_of(there.image), np.zeros(256)
    for region in regions(observed[view], *facing)[:8]:
        matcher = Matcher(region, view, observed, drawings, dims)
        heights = matcher.present[None, :]
        tone_match, line_match = matcher.judge(heights, None, facing[1])
        if np.isfinite(tone_match[0] * line_match[0]) and tone_match[0] >= MIN_TONE_MATCH and line_match[0] >= 0.9:
            rows, cols, inside = matcher.land(heights)
            use = inside[0] & ~matcher.is_border & there.mask[rows[0], cols[0]] & ~matcher.iso_lines[rows[0], cols[0]]
            counts += np.bincount(tones[rows[0][use], cols[0][use]], minlength=256) * (region.sum() / max(1, use.sum()))
    return _peak(counts) if counts.sum() > 0 else None


def iso_tones(observed: dict, drawings, dims: dict) -> dict:
    """For every straight view, the tone its isometric view gives the faces looking at that camera
    (tone, spread), or nothing when it cannot be told.

    It is measured twice: under all faces of the solid with that direction, and under the confirmed
    ones only. Each way fails sometimes. Where the solid has far more material than the part, most
    of its faces are not faces of the part (eval part 51). A region taken for a face square to the
    view can be a slightly tilted one that the isometric view shows in another side's tone (eval part
    171). So where the two disagree, the one that equals the tone already settled for another side
    of the same isometric view is dropped: three sides, three tones."""
    cached = getattr(drawings, "tones", None)
    if cached is not None:
        return cached
    measured = {}
    for view in verify.ORTHO_VIEWS:
        if view not in observed or iso_for(view) not in observed:
            continue
        towards = verify.camera(view)[2]
        facing = facing_tone(view, observed, drawings)
        if facing is not None:
            measured[view] = (face_tone(observed[iso_for(view)], drawings[iso_for(view)], towards),
                              _confirmed_tone(view, observed, drawings, dims, facing))

    def same(a, b) -> bool:
        return a is not None and b is not None and abs(a[0] - b[0]) <= max(a[1], b[1]) + 2

    result = {view: confirmed for view, (overall, confirmed) in measured.items() if same(overall, confirmed)}
    settled = dict(result)
    for view, (overall, confirmed) in measured.items():
        if view in settled:
            continue
        others = [tone for other, tone in settled.items() if other != view and iso_for(other) == iso_for(view)]
        left = [tone for tone in (confirmed, overall) if tone is not None and not any(same(tone, o) for o in others)]
        if len(left) == 1:
            result[view] = left[0]
    # two sides of one isometric view never share a tone: if two came out alike, the one measured
    # only one way is wrong, and if neither is settled both are dropped
    for view in list(result):
        for other in list(result):
            if view < other and iso_for(view) == iso_for(other) and view in result and other in result \
                    and same(result[view], result[other]):
                for name in (view, other):
                    if name not in settled or (view in settled and other in settled):
                        result.pop(name, None)
    try:
        drawings.tones = result
    except AttributeError:
        pass
    return result


def find(view: str, observed: dict, drawings: dict, dims: dict, known: dict | None = None) -> list[Pocket]:
    """The flat faces of one straight view that lie deeper than the solid's surface."""
    iso_name = iso_for(view)
    if view not in observed or iso_name not in observed:
        return []
    right, up, towards = verify.camera(view)
    facing = facing_tone(view, observed, drawings)
    seen_from_iso = iso_tones(observed, drawings, dims).get(view) if facing else None
    if facing is None or seen_from_iso is None:
        return []
    tone, spread = seen_from_iso
    here = observed[view]
    half = here.mask.shape[0] / 2
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    pockets = []
    for region in regions(here, *facing):
        if confirmed(Matcher(region, view, observed, drawings, dims), facing[1]):
            continue
        result = sweep(region, view, observed, drawings, dims, tone, spread)
        if result is None:
            continue
        depths, tone_match, line_match, top = result
        score = tone_match * line_match
        if not np.isfinite(score).any():
            continue
        best = int(np.nanargmax(score))
        drop = depths[0] - depths[best]
        now = score[0] if np.isfinite(score[0]) else 0.0
        if (drop < MIN_DROP_PX * here.mm_per_px or tone_match[best] < MIN_TONE_MATCH
                or line_match[best] < MIN_LINE_MATCH or score[best] - now < MIN_GAIN):
            continue
        # the region up to the middle of the lines around it
        mask = cv2.dilate(region.astype(np.uint8), np.ones((5, 5), np.uint8)) & here.mask.astype(np.uint8)
        to_mm = lambda p: np.column_stack([(p[:, 0] + 0.5 - half) * here.mm_per_px,
                                           (half - 0.5 - p[:, 1]) * here.mm_per_px])
        outlines = vh.trace_outlines(mask * 255, to_mm, here.mm_per_px, known)
        if not outlines:
            continue
        margin = 0.05 * max(dims.values())
        silhouette = vh.Silhouette(name=f"pocket_{view}", views=[view], mask=np.zeros((1, 1), np.uint8),
                                   outlines=outlines, origin=centre + (top + margin) * towards, right=right,
                                   up=up, direction=-towards, depth=float(top + margin - depths[best]))
        above = drawings[view].depth[region]
        removes = float(np.clip(above[np.isfinite(above)] - depths[best], 0, None).sum()) * here.mm_per_px ** 2
        pockets.append(Pocket(view, float(top - depths[best]), float(drop), int(region.sum()), float(tone_match[best]),
                              float(line_match[best]), float(score[best] - now), removes, region=region, silhouette=silhouette))
    return pockets


class Matcher:
    """One region of a straight view, as points that can be put at any height and looked up in the
    isometric view. Heights are mm towards the camera from the centre of the box."""

    def __init__(self, region: np.ndarray, view: str, observed: dict, drawings: dict, dims: dict):
        self.here, self.there = observed[view], observed[iso_for(view)]
        self.right, self.up, self.towards = verify.camera(view)
        self.iso_right, self.iso_up, self.iso_towards = verify.camera(iso_for(view))
        self.top = float(abs(self.towards) @ np.array([dims["x"], dims["y"], dims["z"]])) / 2
        self.region, self.surface = region, drawings[view].depth
        half = region.shape[0] / 2
        inner = cv2.erode(region.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        self.grown = cv2.dilate(region.astype(np.uint8), np.ones((13, 13), np.uint8)).astype(bool)
        border = border_of(region, self.here.lines)
        rows_in, cols_in = _pick(inner if inner.sum() >= MIN_VISIBLE else region, 400)
        rows_bd, cols_bd = _pick(border, 300)
        self.rows, self.cols = np.concatenate([rows_in, rows_bd]), np.concatenate([cols_in, cols_bd])
        self.inner_count, self.border_count = len(rows_in), len(rows_bd)
        self.is_border = np.arange(len(self.rows)) >= len(rows_in)
        self.a = (self.cols + 0.5 - half) * self.here.mm_per_px
        self.b = (half - 0.5 - self.rows) * self.here.mm_per_px
        planar = np.outer(self.a, self.right) + np.outer(self.b, self.up)
        self.iso_a, self.iso_b = planar @ self.iso_right, planar @ self.iso_up
        self.iso_tone = tone_of(self.there.image)
        self.iso_lines = cv2.dilate(self.there.lines.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        self.near_line = cv2.distanceTransform((~self.there.lines).astype(np.uint8), cv2.DIST_L2, 3) <= LINE_NEAR_PX
        present = self.surface[self.rows, self.cols]
        self.present = np.where(np.isfinite(present), present, self.top)

    def land(self, heights: np.ndarray):
        """Pixel (row, column) of every point in the isometric view, and whether it is in the image."""
        size = self.there.mask.shape[0]
        cols = np.rint((self.iso_a + heights * (self.towards @ self.iso_right)) / self.there.mm_per_px + size / 2 - 0.5).astype(int)
        rows = np.rint(size / 2 - 0.5 - (self.iso_b + heights * (self.towards @ self.iso_up)) / self.there.mm_per_px).astype(int)
        inside = (cols >= 0) & (cols < size) & (rows >= 0) & (rows < size)
        return np.clip(rows, 0, size - 1), np.clip(cols, 0, size - 1), inside

    def outline_on_lines(self, heights: np.ndarray) -> np.ndarray:
        """Share of the border points that land on a line, visible or not: a quick first sieve.
        `heights` is (hypotheses, border points)."""
        size = self.there.mask.shape[0]
        a, b = self.iso_a[self.is_border], self.iso_b[self.is_border]
        cols = np.rint((a + heights * (self.towards @ self.iso_right)) / self.there.mm_per_px + size / 2 - 0.5).astype(int)
        rows = np.rint(size / 2 - 0.5 - (b + heights * (self.towards @ self.iso_up)) / self.there.mm_per_px).astype(int)
        inside = (cols >= 0) & (cols < size) & (rows >= 0) & (rows < size)
        hit = self.near_line[np.clip(rows, 0, size - 1), np.clip(cols, 0, size - 1)] & inside
        return hit.mean(axis=1)

    def judge(self, heights: np.ndarray, tone: int | None, spread: int):
        """Tone match and line match of every hypothesis; `heights` is (hypotheses, points). With
        `tone` None the visible points only have to share one tone, whichever it is."""
        heights = np.atleast_2d(heights)
        rise = float(self.iso_towards @ self.towards)
        drift = np.array([self.iso_towards @ self.right, self.iso_towards @ self.up]) / self.here.mm_per_px
        reach = (self.top - heights) / rise
        steps = int(np.clip(np.abs(drift).max() * reach.max(), 8, 64))
        visible = np.ones(heights.shape, bool)
        blocking = np.where(np.isfinite(self.surface), self.surface, -np.inf)
        limit = self.region.shape[0] - 1
        for index in range(1, steps + 1):
            along = reach * index / steps
            c = np.rint(self.cols[None, :] + along * drift[0]).astype(int)
            r = np.rint(self.rows[None, :] - along * drift[1]).astype(int)
            ok = (c >= 0) & (c <= limit) & (r >= 0) & (r <= limit)
            c, r = np.clip(c, 0, limit), np.clip(r, 0, limit)
            visible &= ~ok | self.grown[r, c] | (blocking[r, c] <= heights + along * rise + 0.5 * self.here.mm_per_px)
        rows, cols, inside = self.land(heights)
        visible &= inside
        judged = visible & ~self.is_border[None, :] & ~self.iso_lines[rows, cols] & self.there.mask[rows, cols]
        seen_part = (visible & ~self.is_border[None, :] & ~self.iso_lines[rows, cols]).sum(axis=1)
        tones = self.iso_tone[rows, cols]
        if tone is None:
            shows = np.zeros(heights.shape, bool)
            for index in range(len(heights)):
                if judged[index].sum() >= MIN_VISIBLE:
                    counts = np.bincount(tones[index][judged[index]], minlength=256).astype(float)
                    mode = int(np.convolve(counts, np.ones(2 * spread + 1), mode="same").argmax())
                    shows[index] = np.abs(tones[index] - mode) <= spread
        else:
            shows = np.abs(tones - tone) <= spread
        enough = seen_part >= max(MIN_VISIBLE, MIN_VISIBLE_SHARE * self.inner_count)
        tone_match = np.where(enough, (judged & shows).sum(axis=1) / np.maximum(seen_part, 1), np.nan)
        edge = visible & self.is_border[None, :]
        seen_edge = edge.sum(axis=1)
        line_match = np.where(seen_edge >= max(MIN_VISIBLE_BORDER, MIN_VISIBLE_SHARE * self.border_count),
                              (edge & self.near_line[rows, cols]).sum(axis=1) / np.maximum(seen_edge, 1), np.nan)
        return tone_match, line_match


def find_deep(view: str, observed: dict, drawings: dict, dims: dict, skip: tuple = (), known: dict | None = None) -> list[Pocket]:
    """Floors the first search cannot judge: their outline in the straight view is the outline of
    something nearer, so it lands on no line, or everything around them is still too high for any
    point to be certainly visible.

    Here only the tone decides. For every depth, the share of the region that lands on pixels of the
    right tone is counted, visible or not; the depth where that share is highest is taken (the
    shallowest, if several are as good). Then all floors found this way are lowered together, and each
    is kept only if the points that are now certainly visible show the tone."""
    iso_name = iso_for(view)
    if view not in observed or iso_name not in observed:
        return []
    right, up, towards = verify.camera(view)
    facing = facing_tone(view, observed, drawings)
    seen_from_iso = iso_tones(observed, drawings, dims).get(view) if facing else None
    if facing is None or seen_from_iso is None:
        return []
    tone, spread = seen_from_iso
    here, there = observed[view], observed[iso_name]
    surface = drawings[view].depth
    step = there.mm_per_px / np.sqrt(2 / 3)
    candidates = []
    for region in regions(here, *facing):
        if region.sum() < DEEP_MIN_AREA_PX or any((region & other).any() for other in skip):
            continue  # with tone alone a small region finds a match at some depth by chance
        present = surface[region]
        present = present[np.isfinite(present)]
        if not len(present):
            continue
        matcher = Matcher(region, view, observed, drawings, dims)
        if confirmed(matcher, facing[1]):
            continue
        start, lowest = float(np.percentile(present, 95)), -matcher.top + 2 * here.mm_per_px
        depths = np.linspace(start, lowest, int(min(MAX_DEPTHS, max(2, (start - lowest) / step + 1))))
        rows, cols, inside = matcher.land(np.repeat(depths[:, None], len(matcher.rows), axis=1))
        shows = inside & there.mask[rows, cols] & (np.abs(matcher.iso_tone[rows, cols] - tone) <= spread)
        share = shows[:, ~matcher.is_border].mean(axis=1)
        if share.max() < MIN_SUPPORT or share.max() - share[0] < MIN_GAIN:
            continue
        best = int(np.argmax(share >= share.max() - PLATEAU))
        drop_px = (depths[0] - depths[best]) / here.mm_per_px
        if drop_px < MIN_DROP_PX or drop_px > DEEP_MAX_DROP * np.sqrt(region.sum()):
            continue  # a floor that far down a hole that narrow would not be in sight
        candidates.append((region, matcher, float(depths[best]), float(share[best]), float(share[0])))

    for _ in range(3):  # lower them together; drop what the tone then contradicts; check the rest again
        lowered = surface.copy()
        for region, _, depth, _, _ in candidates:
            column = cv2.dilate(region.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
            lowered[column] = np.minimum(lowered[column], depth)
        kept = []
        for candidate in candidates:
            matcher, depth = candidate[1], candidate[2]
            matcher.surface = lowered
            tone_match, _ = matcher.judge(np.full((1, len(matcher.rows)), depth), tone, spread)
            if np.isfinite(tone_match[0]):
                if tone_match[0] >= MIN_TONE_MATCH:
                    kept.append(candidate)
            elif candidate[3] >= MIN_TONE_MATCH and candidate[3] - candidate[4] >= SURE_GAIN:
                # Nothing is certainly visible, so nothing can contradict it. It is still kept when
                # nearly the whole region shows the tone there and far less of it does at the surface:
                # a large floor that is in plain sight of the isometric camera.
                kept.append(candidate)
        if len(kept) == len(candidates):
            break
        candidates = kept

    half = here.mask.shape[0] / 2
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    margin = 0.05 * max(dims.values())
    pockets = []
    for region, matcher, depth, share, now in candidates:
        mask = cv2.dilate(region.astype(np.uint8), np.ones((5, 5), np.uint8)) & here.mask.astype(np.uint8)
        to_mm = lambda p: np.column_stack([(p[:, 0] + 0.5 - half) * here.mm_per_px,
                                           (half - 0.5 - p[:, 1]) * here.mm_per_px])
        outlines = vh.trace_outlines(mask * 255, to_mm, here.mm_per_px, known)
        if not outlines:
            continue
        top = matcher.top
        silhouette = vh.Silhouette(name=f"deep_{view}", views=[view], mask=np.zeros((1, 1), np.uint8),
                                   outlines=outlines, origin=centre + (top + margin) * towards, right=right,
                                   up=up, direction=-towards, depth=float(top + margin - depth))
        above = surface[region]
        above = above[np.isfinite(above)]
        removes = float(np.clip(above - depth, 0, None).sum()) * here.mm_per_px ** 2
        pockets.append(Pocket(view, float(top - depth), float(np.percentile(above, 95) - depth), int(region.sum()),
                              share, 0.0, share - now, removes, region=region, silhouette=silhouette))
    return pockets


def confirmed(matcher: "Matcher", spread: int) -> bool:
    """Whether the solid's surface under a region already agrees with the isometric view as it is:
    the region's outline, at the heights the solid has there, lands on lines, and its pixels share one
    tone. Such a face is left alone, whatever else would fit. A gently sloping face has almost the
    tone of one square to the view, and taken for a flat floor it found a depth that fitted (eval part
    171); but the solid had the slope right, from the silhouette of the side view."""
    tone_match, line_match = matcher.judge(matcher.present[None, :], None, spread)
    return bool(np.isfinite(tone_match[0] * line_match[0]) and tone_match[0] >= MIN_TONE_MATCH and line_match[0] >= 0.9)


def slanted_regions(observed: verify.Observed, tone: int, spread: int) -> list[np.ndarray]:
    """Flat faces not square to the view: areas of one even tone other than the facing one, enclosed by lines."""
    lines = cv2.dilate(observed.lines.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    tones = tone_of(observed.image)
    other = observed.mask & ~lines & (np.abs(tones - tone) > spread)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(other.astype(np.uint8), connectivity=4)
    border_ok = lines | ~observed.mask
    found = []
    for label in np.argsort(-stats[1:, cv2.CC_STAT_AREA])[:4 * MAX_SLANTED] + 1:
        if stats[label, cv2.CC_STAT_AREA] < MIN_REGION_PX:
            break
        region = labels == label
        inside = tones[region]
        if np.percentile(inside, 92) - np.percentile(inside, 8) > 2 * spread:
            continue  # a curved face: the tone changes across it
        ring = cv2.dilate(region.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~region
        if ring.sum() and (ring & border_ok).sum() / ring.sum() >= MIN_ENCLOSED:
            found.append(region)
        if len(found) == MAX_SLANTED:
            break
    return found


def anchors(region: np.ndarray, observed: verify.Observed, surface: np.ndarray, tone: int, spread: int) -> list[tuple]:
    """Where a slanted face hangs on a face whose depth is known. A slanted plane meets a face square
    to the view in a level line, which the view shows as a straight edge of the region. For each long
    straight edge with such a face just outside it: (a point of the edge in pixels (column, row), the
    unit direction into the region in (a right, b up), the depth of the neighbour in mm)."""
    contours, _ = cv2.findContours(region.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return []
    corners = cv2.approxPolyDP(max(contours, key=cv2.contourArea), 2.0, True).reshape(-1, 2).astype(float)
    edges = np.roll(corners, -1, axis=0) - corners
    lengths = np.linalg.norm(edges, axis=1)
    tones = tone_of(observed.image)
    size = region.shape[0]
    found = []
    for index in np.argsort(-lengths)[:5]:
        if lengths[index] < max(12.0, 0.25 * lengths.max()):
            break
        along = edges[index] / lengths[index]
        normal = np.array([-along[1], along[0]])                     # in pixels (column, row)
        middle = corners[index] + edges[index] / 2
        probe = np.rint(middle + 4 * normal).astype(int)
        if not (0 <= probe[0] < size and 0 <= probe[1] < size):
            continue
        if not region[probe[1], probe[0]]:
            normal = -normal                                         # now pointing into the region
        # the neighbour, sampled a few pixels outside the edge, beyond the line drawn on it
        steps = np.linspace(0.15, 0.85, 24)[:, None]
        outside = np.rint(corners[index] + steps * edges[index] - 5 * normal).astype(int)
        ok = (outside >= 0).all(axis=1) & (outside < size).all(axis=1)
        cols, rows = outside[ok, 0], outside[ok, 1]
        flat = observed.mask[rows, cols] & ~region[rows, cols] & (np.abs(tones[rows, cols] - tone) <= spread) \
            & np.isfinite(surface[rows, cols])
        if flat.sum() < 12:
            continue
        depths = surface[rows, cols][flat]
        depth = float(np.median(depths))
        if np.abs(depths - depth).mean() > 1.5 * observed.mm_per_px:
            continue  # the neighbour is not one level
        found.append((middle, np.array([normal[0], -normal[1]]), depth))
    return found


def find_slanted(view: str, observed: dict, drawings: dict, dims: dict, known: dict | None = None) -> list[Pocket]:
    """The slanted flat faces of one straight view that lie below the solid's surface."""
    iso_name = iso_for(view)
    if view not in observed or iso_name not in observed:
        return []
    right, up, towards = verify.camera(view)
    facing = facing_tone(view, observed, drawings)
    if facing is None:
        return []
    here = observed[view]
    half = here.mask.shape[0] / 2
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    spread = facing[1]
    slopes = np.tan(np.radians(SLOPES_DEG))
    pockets = []
    for region in slanted_regions(here, *facing):
        matcher = Matcher(region, view, observed, drawings, dims)
        top = matcher.top
        # every plane through an edge at its neighbour's depth: height = depth - slope * (distance into the region)
        planes, heights = [], []
        for middle, inward, depth in anchors(region, here, drawings[view].depth, *facing):
            a0, b0 = (middle[0] + 0.5 - half) * here.mm_per_px, (half - 0.5 - middle[1]) * here.mm_per_px
            reach = (matcher.a - a0) * inward[0] + (matcher.b - b0) * inward[1]
            for slope in np.concatenate([slopes, -slopes]):               # falling away, or rising
                height = depth - slope * reach
                if height.max() <= top + here.mm_per_px and height.min() >= -top - here.mm_per_px:
                    planes.append((a0, b0, inward[0], inward[1], slope, depth))
                    heights.append(height)
        if not planes:
            continue
        heights = np.array(heights)
        tone_match, line_match = matcher.judge(heights, None, spread)
        score = tone_match * line_match
        if not np.isfinite(score).any():
            continue
        best = int(np.nanargmax(score))
        now_tone, now_line = matcher.judge(matcher.present[None, :], None, spread)
        now = float(now_tone[0] * now_line[0]) if np.isfinite(now_tone[0] * now_line[0]) else 0.0
        drop = float(np.clip(matcher.present - heights[best], 0, None)[~matcher.is_border].mean())
        if (drop < MIN_DROP_PX * here.mm_per_px or tone_match[best] < MIN_TONE_MATCH
                or line_match[best] < MIN_LINE_MATCH_SLANTED or score[best] - now < MIN_GAIN):
            continue
        a0, b0, ua, ub, slope, depth = planes[best]
        mask = cv2.dilate(region.astype(np.uint8), np.ones((5, 5), np.uint8)) & here.mask.astype(np.uint8)
        to_mm = lambda p: np.column_stack([(p[:, 0] + 0.5 - half) * here.mm_per_px,
                                           (half - 0.5 - p[:, 1]) * here.mm_per_px])
        outlines = vh.trace_outlines(mask * 255, to_mm, here.mm_per_px, known)
        if not outlines:
            continue
        margin = 0.05 * max(dims.values())
        point = centre + a0 * right + b0 * up + depth * towards
        normal = towards + slope * (ua * right + ub * up)
        silhouette = vh.Silhouette(name=f"slanted_{view}", views=[view], mask=np.zeros((1, 1), np.uint8),
                                   outlines=outlines, origin=centre + (top + margin) * towards, right=right,
                                   up=up, direction=-towards, depth=float(2 * (top + margin)),
                                   info={"plane": (tuple(map(float, point)), tuple(map(float, normal / np.linalg.norm(normal))))})
        rows, cols = np.nonzero(region)
        reach = ((cols + 0.5 - half) * here.mm_per_px - a0) * ua + ((half - 0.5 - rows) * here.mm_per_px - b0) * ub
        above = drawings[view].depth[rows, cols]
        removes = float(np.clip(np.where(np.isfinite(above), above, -np.inf) - (depth - slope * reach), 0, None).sum()) * here.mm_per_px ** 2
        pockets.append(Pocket(view, float(top - depth), drop, int(region.sum()), float(tone_match[best]),
                              float(line_match[best]), float(score[best] - now), removes,
                              float(np.degrees(np.arctan(slope))), silhouette=silhouette))
    return pockets


def _tool(silhouette: vh.Silhouette) -> TopoDS_Shape | None:
    """What is cut away: the column above a region, down to its floor or to its slanted plane."""
    column = vh.prism(silhouette)
    plane = silhouette.info.get("plane")
    if column is None or plane is None:
        return column
    point, normal = plane
    face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*point), gp_Dir(*normal))).Face()
    above = BRepPrimAPI_MakeHalfSpace(face, gp_Pnt(*(np.array(point) + 1e3 * np.array(normal)))).Solid()
    common = BRepAlgoAPI_Common(column, above)
    return common.Shape() if common.IsDone() else None


def cut_pockets(shape: TopoDS_Shape, silhouettes: list) -> TopoDS_Shape | None:
    """Cut the columns above the listed regions out of `shape`. Runs in a child process."""
    tools = List_TopoDS_Shape()
    for silhouette in silhouettes:
        tool = _tool(silhouette)
        if tool is not None:
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


class Drawings:
    """The solid drawn from the cameras of the views, each view once, until the solid changes."""

    def __init__(self, solid: TopoDS_Shape, observed: dict, dims: dict):
        self.observed, self.dims, self.drawn = observed, dims, {}
        self.mesh = verify.tessellate(solid, 0.3 * min(o.mm_per_px for o in observed.values()))

    def __getitem__(self, name: str) -> verify.Drawing:
        if name not in self.drawn:
            view = self.observed[name]
            self.drawn[name] = verify.draw(self.mesh, name, self.dims, view.mm_per_px, view.mask.shape[0])
        return self.drawn[name]


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step: for every straight view, find the flat faces that lie deeper and cut down to them."""
    with trace.step("shading_depth", kind="code") as step:
        if case_dir is None:
            return solid
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        if target and vh.volume(solid) < MIN_EXCESS * target:
            step.log("the solid already has the part's volume; nothing to look for")
            return solid
        observed = verify.observe(case_dir, dims)
        if not any(name.startswith("iso") for name in observed):
            step.log("no isometric view with a known scale; nothing to match depths against")
            return solid
        score = verify.check(solid, case_dir, observed).score
        before, current, kept, found, drawings = score, solid, [], 0, None
        rater = judge.Judge(case_dir, dims, observed) if judge.local() else None

        def attempt(base, pockets, rating):
            carved = agent._guarded(step, base, target, lambda: agent.cutter.run(
                cut_pockets, base, [p.silhouette for p in pockets], TIMEOUT), quiet=True)
            if carved is None or vh.volume(carved) >= vh.volume(base) * (1 - 1e-6):
                return None, rating
            if rater is not None:
                verdict = rater.rate(base, carved)
                judge.note(agent, "shading", base, carved, verdict.accepted, verdict)
                if not verdict.accepted:
                    step.log(f"{verdict.reason} ({verdict}); not kept", level="warning")
                    return None, rating
                return carved, rating
            after = verify.check(carved, case_dir, observed).score
            judge.note(agent, "shading", base, carved, after >= rating - SCORE_SLACK)
            if after < rating - SCORE_SLACK:
                step.log(f"the verifier rates the result lower ({rating:.3f} to {after:.3f}); not kept", level="warning")
                return None, rating
            return carved, after

        def spent() -> bool:
            return agent.out_of_time()

        for _ in range(MAX_PASSES):
            cut_before = len(kept)
            for view in verify.ORTHO_VIEWS:
                if spent():
                    break
                if view not in observed:
                    continue
                if drawings is None:
                    drawings = Drawings(current, observed, dims)
                pockets = find(view, observed, drawings, dims)
                if DEEP:
                    pockets += find_deep(view, observed, drawings, dims, skip=tuple(p.region for p in pockets))
                if SLANTED:
                    pockets += find_slanted(view, observed, drawings, dims)
                found += len(pockets)
                if target:  # the solid cannot hold more excess than its volume above the part's
                    excess = vh.volume(current) - MIN_SHARE * target
                    pockets = [p for p in pockets if p.removes <= excess]
                if not pockets:
                    continue
                carved, rating = attempt(current, pockets, score)
                accepted = pockets
                if carved is None and len(pockets) > 1:
                    carved, accepted = current, []
                    for pocket in sorted(pockets, key=lambda p: -p.gain)[:6]:
                        if spent():
                            break
                        result, rating = attempt(carved, [pocket], rating)
                        if result is not None:
                            carved, accepted = result, accepted + [pocket]
                if carved is not None and accepted:
                    current, score, kept, drawings = carved, rating, kept + accepted, None
            if len(kept) == cut_before or spent():
                break
        if spent():
            step.log("stopped: out of time", level="warning")
        step.metric(regions_found=found, regions_cut=len(kept))
        if not kept:
            step.log("no flat face was found deeper than the solid's surface")
            return solid
        step._status = None
        step.attach("pockets.json", [p.summary() for p in kept])
        step.metric(score_before=round(before, 4), score_after=round(score, 4))
        agent._record(step, current, "shading")
        return current
