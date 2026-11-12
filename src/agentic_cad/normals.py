"""Is a flat area of a straight view square to the camera or slanted? The isometric view decides.

One tone is one equation for a normal, and several directions share the tone of a face square to
the camera (regions.py calls a third of the slanted area "facing" for that reason). The isometric
view that sees the same side shows the same face from another direction, in another tone.

Finding the face in the isometric view needs its depth, which is taken from the solid: the points
of the solid's surface under the area are projected into the isometric view, and only those the
solid itself shows there (nothing of it in front) are read. With the tone table of the renderer
(tone.py) the test is then: do both tones fit the normal the solid has there? If so the area is
that face. If not, the solid is wrong under the area, or hidden there, and the area keeps the kind
its tone alone gave it. Nothing here cuts.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from agentic_cad import regions, tone, verify

MAX_POINTS = 600        # points of an area that are looked up in the isometric view
MIN_SEEN = 30           # visible points needed to read a tone there
DEPTH_PX = 2.0          # a point is visible where the solid drawn from the isometric camera is this close to it
EVEN_SPREAD = 8         # the tones read in the isometric view must stay within this (8th to 92nd percentile)
CONFIRM = 5.0           # grey levels: the solid's face is confirmed if its tone is this close in both views
FLAT_DEG = 3.0          # a face is flat, and square to the camera, within this many degrees
TILT_STEP = 2.0         # degrees between the tilts tried
TURN_STEP = 10.0        # degrees between the directions of tilt tried


def iso_for(view: str) -> str:
    """The isometric view that sees the faces looking at this straight camera."""
    towards = verify.camera(view)[2]
    return max(("iso_front_right_top", "iso_back_left_bottom"), key=lambda iso: verify.camera(iso)[2] @ towards)


@dataclass
class Second:
    tone: float      # median tone of the area's points in the isometric view
    spread: float    # 8th to 92nd percentile of those tones
    seen: int        # points that could be read
    solid_normal: np.ndarray | None = None   # mean normal of the solid's surface under the area, frame of the solid
    solid_bend: float = 0.0                  # degrees within which nine tenths of the solid's normals lie around it


def second_tone(area: np.ndarray, view: str, observed: dict, drawings, dims: dict[str, float]) -> Second | None:
    """The tone the isometric view shows where the solid's surface under `area` (bool image of the
    straight view) lands. None if too little of it is in sight there."""
    iso = iso_for(view)
    if iso not in observed or view not in observed:
        return None
    here, there = observed[view], observed[iso]
    right, up, towards = verify.camera(view)
    iso_right, iso_up, iso_towards = verify.camera(iso)
    rows, cols = np.nonzero(area & regions._readable(here) & drawings[view].mask)
    if len(rows) > MAX_POINTS:
        keep = np.random.default_rng(0).choice(len(rows), MAX_POINTS, replace=False)
        rows, cols = rows[keep], cols[keep]
    if len(rows) < MIN_SEEN:
        return None
    half, iso_half = here.mask.shape[0] / 2, there.mask.shape[0] / 2
    # the points in 3D, from the centre of the box
    points = (np.outer((cols + 0.5 - half) * here.mm_per_px, right) + np.outer((half - 0.5 - rows) * here.mm_per_px, up)
              + np.outer(drawings[view].depth[rows, cols], towards))
    iso_cols = np.rint(points @ iso_right / there.mm_per_px + iso_half - 0.5).astype(int)
    iso_rows = np.rint(iso_half - 0.5 - points @ iso_up / there.mm_per_px).astype(int)
    size = there.mask.shape[0]
    inside = (iso_cols >= 0) & (iso_cols < size) & (iso_rows >= 0) & (iso_rows < size)
    iso_cols, iso_rows = np.clip(iso_cols, 0, size - 1), np.clip(iso_rows, 0, size - 1)
    drawn = drawings[iso]
    clear = ~(cv2.dilate((there.lines | drawn.edges).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
    with np.errstate(invalid="ignore"):
        in_sight = np.abs(drawn.depth[iso_rows, iso_cols] - points @ iso_towards) <= DEPTH_PX * there.mm_per_px
    usable = inside & in_sight & there.mask[iso_rows, iso_cols] & clear[iso_rows, iso_cols]
    if usable.sum() < MIN_SEEN:
        return None
    tones = regions.tone_of(there.image)[iso_rows[usable], iso_cols[usable]]
    low, middle, high = np.percentile(tones, (8, 50, 92))
    under = drawings[view].normal[rows, cols]
    mean = under.mean(axis=0)
    mean /= max(float(np.linalg.norm(mean)), 1e-9)
    bend = float(np.percentile(np.degrees(np.arccos(np.clip(under @ mean, -1, 1))), 90))
    return Second(float(middle), float(high - low), int(usable.sum()), mean, bend)


def _candidates() -> np.ndarray:
    """Normals in the camera's frame of the straight view: square to it first, then every tilt and turn."""
    tilt = np.radians(np.arange(TILT_STEP, 88.0, TILT_STEP))[:, None]
    turn = np.radians(np.arange(0.0, 360.0, TURN_STEP))[None, :]
    tilted = np.stack([np.sin(tilt) * np.cos(turn), np.sin(tilt) * np.sin(turn), np.cos(tilt) * np.ones_like(turn)], axis=2)
    return np.concatenate([[[0.0, 0.0, 1.0]], tilted.reshape(-1, 3)])


@dataclass
class Solved:
    kind: str | None      # "facing" or "slanted" when the two tones confirm the solid's face, else None
    normal: np.ndarray    # the confirmed normal (the solid's), else the best guess from the tones alone;
                          # in the frame of the solid (x, y, z), unit, towards the straight camera
    tilt: float           # degrees between that normal and the direction to the straight camera
    misfit: float         # grey levels the two tones are off for the solid's normal, the worse of the two views
    guess_misfit: float   # ... for the best guess from the tones alone, summed over the two views
    facing_misfit: float  # ... for a face square to the straight camera, summed


def solve(table: tone.Table, view: str, first_tone: float, second: Second) -> Solved:
    """Do the tones of a flat area in the straight view and in the isometric one fit the face the
    solid has there? Then the area is that face: square to the camera or slanted, with its normal.

    The two tones alone do not settle it (measured on the 24 eval parts): they have more than one
    solution, and where the solid is at the wrong depth the isometric tone is read off another face.
    Every normal they gave correctly was also the solid's own, so the solid's normal is what is
    tested. The free solution is still returned as a guess when the solid's face is not confirmed."""
    frame, iso_frame = np.stack(verify.camera(view)), np.stack(verify.camera(iso_for(view)))
    in_view = _candidates()
    in_solid = in_view @ frame
    in_iso = in_solid @ iso_frame.T
    off_first = np.abs(table.tone(in_view) - first_tone)
    off_second = np.where(in_iso[:, 2] > 0.05, np.abs(table.tone(in_iso) - second.tone), np.inf)  # must be in sight there
    total = off_first + off_second
    guess = int(np.argmin(total))

    own = second.solid_normal
    own_first = abs(float(table.tone(frame @ own)) - first_tone)
    own_second = abs(float(table.tone(iso_frame @ own)) - second.tone) if (iso_frame @ own)[2] > 0.05 else np.inf
    misfit = max(own_first, own_second)
    if second.solid_bend <= FLAT_DEG and misfit <= CONFIRM:
        tilt = float(np.degrees(np.arccos(np.clip((frame @ own)[2], -1, 1))))
        return Solved("facing" if tilt <= FLAT_DEG else "slanted", own, tilt, float(misfit), float(total[guess]), float(total[0]))
    tilt = float(np.degrees(np.arccos(np.clip(in_view[guess, 2], -1, 1))))
    return Solved(None, in_solid[guess], tilt, float(misfit), float(total[guess]), float(total[0]))


def refine(found: dict, observed: dict, drawings, dims: dict[str, float], table: tone.Table | None = None) -> dict:
    """For every region of `regions.find`: (region, Second or None, Solved or None). `Solved.kind` is
    set where the isometric view could be read evenly and confirms the solid's face."""
    table = table or tone.choose(observed, {name: drawings[name] for name in observed})
    result = {}
    for view, (labels, items) in found.items():
        rows = []
        for item in items:
            second = solved = None
            if item.kind != "curved" and table is not None:
                second = second_tone(labels == item.label, view, observed, drawings, dims)
                if second is not None and second.spread <= EVEN_SPREAD:
                    solved = solve(table, view, item.tone, second)
            rows.append((item, second, solved))
        result[view] = rows
    return result
