"""What kind of face is each area of a straight view? Deterministic, needs no solid.

The views draw a dark line on every face boundary, so an area of the part enclosed by lines is one
face (or one visible piece of it). The light sits with the camera and is the same in every view, so
the tone of a pixel depends only on which way the face under it looks (docs/memory/knowledge.md):

- "facing": one even tone, the one of a face square to the camera. A flat face square to the view;
- "slanted": one even tone, but another one. A flat face at an angle;
- "curved": the tone changes across the area.

Nothing here cuts. `find(observed)` gives the regions of every straight view for later steps.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from agentic_cad import verify

MIN_REGION_PX = 250    # smaller areas are not judged
EDGE_PX = 2            # pixels this close to a line are not read: the line darkens them
TONE_JUMP = 24         # neighbouring pixels this far apart in tone lie on two faces
DARK_LINE_MIN = 8      # a line on a dark face is at least this many levels darker than both sides
DARK_LINE_SHARE = 0.15 # ... and this share of their tone
FLAT_SPREAD = 5        # the tones of a flat face stay within this many grey levels (8th to 92nd percentile)
MAX_FALLOFF = 8        # grey levels the uneven light on a flat face may explain, over half the image
FACING_TOLERANCE = 4   # a flat face square to the camera is this close to the facing tone


@dataclass
class Region:
    view: str
    label: int           # its value in the label image of the view
    area: int            # pixels
    box: tuple[int, int, int, int]   # column, row, width, height
    kind: str            # "facing", "slanted" or "curved"
    tone: int            # median tone
    spread: int          # 8th to 92nd percentile of the tone

    def summary(self) -> dict:
        return {"view": self.view, "kind": self.kind, "area_px": self.area, "tone": self.tone,
                "spread": self.spread, "box": list(self.box)}


def tone_of(image: np.ndarray) -> np.ndarray:
    return image.max(axis=2).astype(np.int16)  # the part is tan: its brightest channel carries the shade


def segment(observed: verify.Observed) -> tuple[np.ndarray, np.ndarray]:
    """The areas between the lines: a label per pixel (0: background or line) and OpenCV's statistics
    per label. Remembered on the view: several steps ask for the same one. Do not write into them."""
    known = getattr(observed, "_segments", None)
    if known is not None:
        return known
    # Where a bright face meets a dark one the renderer's line does not stand out and is not found
    # as a line; the jump in tone itself is the boundary there.
    tones = tone_of(observed.image)
    jump = np.zeros(tones.shape, bool)
    jump[:, :-1] |= np.abs(np.diff(tones, axis=1)) >= TONE_JUMP
    jump[:-1, :] |= np.abs(np.diff(tones, axis=0)) >= TONE_JUMP
    # Between two dark faces a line is less than the 28 levels darker that verify.find_lines asks
    # for (practice part 6, the flanks of the fins: tone 55, line 30). It is still far darker than
    # both sides for its own tone: a third of what a small closing fills in.
    bright = observed.image.max(axis=2)
    closed = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)).astype(np.int16)
    faint = (closed - bright >= np.maximum(DARK_LINE_MIN, DARK_LINE_SHARE * closed)) & observed.mask
    open_area = (observed.mask & ~observed.lines & ~jump & ~faint).astype(np.uint8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(open_area, connectivity=4)
    observed._segments = (labels, stats)
    return labels, stats


def _readable(observed: verify.Observed) -> np.ndarray:
    kernel = np.ones((2 * EDGE_PX + 1, 2 * EDGE_PX + 1), np.uint8)
    return observed.mask & ~(cv2.dilate(observed.lines.astype(np.uint8), kernel) > 0)


def _measure(observed: verify.Observed) -> tuple[np.ndarray, list[tuple]]:
    """The label image of a view and, per area large enough, largest first: label, area, box, median
    tone and spread (8th to 92nd percentile). Remembered on the view."""
    known = getattr(observed, "_measured", None)
    if known is not None:
        return known
    labels, stats = segment(observed)
    tones = tone_of(observed.image)
    readable = _readable(observed)
    measured = []
    for label in np.argsort(-stats[1:, cv2.CC_STAT_AREA]) + 1:
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < MIN_REGION_PX:
            break
        x, y, w, h = (int(stats[label, k]) for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        window = (slice(y, y + h), slice(x, x + w))
        inside = labels[window] == label
        core = inside & readable[window]
        values = tones[window][core if core.sum() >= MIN_REGION_PX // 4 else inside]
        low, middle, high = np.percentile(values, (8, 50, 92))
        spread = high - low
        # the falloff is spread over the whole image: a small area can only have a small part of it
        size = observed.mask.shape[0]
        allowed = MAX_FALLOFF * min(1.0, max(w, h) / (size / 2))
        if FLAT_SPREAD < spread <= FLAT_SPREAD + allowed:
            rows, cols = np.nonzero(core if core.sum() >= MIN_REGION_PX // 4 else inside)
            spread = _without_falloff(values, cols + x, rows + y, size, spread, allowed)
        measured.append((int(label), area, (x, y, w, h), int(middle), int(round(spread))))
    observed._measured = (labels, measured)
    return labels, measured


def _without_falloff(tones: np.ndarray, cols: np.ndarray, rows: np.ndarray, size: int, spread: float,
                     allowed: float) -> float:
    """The spread of the tones of an area once the light's falloff is taken out. The organisers'
    renderer lights a flat face a little unevenly: up to about 8 levels over the image, smoothly, as
    a plane plus a bowl around the image centre. If that much explains the area's tones it is flat."""
    if len(tones) > 20000:
        keep = np.random.default_rng(0).choice(len(tones), 20000, replace=False)
        tones, cols, rows = tones[keep], cols[keep], rows[keep]
    u, v = (cols - size / 2) / (size / 2), (rows - size / 2) / (size / 2)
    design = np.c_[np.ones(len(u)), u, v, u * u + v * v]
    fitted = design @ np.linalg.lstsq(design, tones.astype(float), rcond=None)[0]
    if np.ptp(np.percentile(fitted, (8, 92))) > allowed:
        return spread  # more than a falloff: the face turns
    return float(np.ptp(np.percentile(tones - fitted, (8, 92))))


def facing_tone(measured: dict[str, list[tuple]]) -> int | None:
    """The tone of a face square to the camera. The light sits with the camera, so it is one tone for
    all six straight views. It is the tone that most of the flat area of the six views has: a straight
    view of a part shows more flat faces square to it than flat faces at any one other angle. Pixels
    are not counted, only even areas: a round part seen from the side has no flat face there at all,
    and its commonest tone is some band of its curved surface."""
    counts = np.zeros(256)
    for items in measured.values():
        for _, area, _, tone, spread in items:
            if spread <= FLAT_SPREAD:
                counts[tone] += area
    if not counts.any():
        return None
    return int(np.convolve(counts, np.ones(2 * FACING_TOLERANCE + 1), mode="same").argmax())


def kind_of(tone: int, spread: int, facing: int | None) -> str:
    if spread > FLAT_SPREAD:
        return "curved"
    return "facing" if facing is not None and abs(tone - facing) <= FACING_TOLERANCE else "slanted"


def find(observed: dict[str, verify.Observed]) -> dict[str, tuple[np.ndarray, list[Region]]]:
    """Label image and regions (largest first) of every straight view."""
    labels, measured = {}, {}
    for name in verify.ORTHO_VIEWS:
        if name in observed:
            labels[name], measured[name] = _measure(observed[name])
    facing = facing_tone(measured)
    return {name: (labels[name], [Region(name, label, area, box, kind_of(tone, spread, facing), tone, spread)
                                  for label, area, box, tone, spread in items])
            for name, items in measured.items()}
