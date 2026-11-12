"""One flat face in the render may not get new edges from a cut.

An area of a view between the render's lines whose tone is one colour (no gradient beyond the
renderer's slow light fall-off across the image) is one plane of the part: the render draws a line
on every face boundary, and any bend or curve would change the tone. A cut that draws a new edge of
our solid inside such an area carves into a face the part has whole (user, 2026-11-12: practice 6,
whose flanks are flat triangles from flange to cap, got triangles and steps cut into them by the
level-faces step).

What counts as one colour: after a plane is fitted to the tone over the area (the renderer's
fall-off is close to linear), 90 % of the pixels lie within RESIDUAL levels of it, and the fitted
plane rises by at most SLOPE levels over 100 px. Measured on the 9 practice renders: flat panels of
practice 6 0.9 to 1.5 residual and under 1.4 slope; its gently curved fins 8.2 and 8.7; flanges and
caps 55 to 88.

The check compares the candidate with the solid it was cut from, view by view: only edges the
candidate has and the solid before did not count, and only deeper than MARGIN_PX inside the area
(our outline may differ from the render by a pixel or two along a line). A cut is refused when more
than MIN_PX of its new edge pixels, summed over the 8 views, lie inside one-colour areas and they are
more than MAX_SHARE of all its new edge pixels. A right cut opens a feature whose outline the render
draws, so most of its new edges lie on lines; it may still leave small wrong bits behind (practice 7's
top cavity: 8 % inside, from a bore stub its floor uncovers), which do not outweigh it. Practice 6's
level-face cuts drew 39 % inside (11000 to 18000 px in each side view). Practice 5's bowls drew none.
"""

from __future__ import annotations

import os

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import regions, verify

ENABLED = os.environ.get("HULL_FLAT_GUARD", "1") != "0"
RESIDUAL = 3.0      # tone levels, 5th to 95th percentile around the fitted plane
SLOPE = 2.5         # tone levels per 100 px of the fitted plane
MIN_AREA_PX = 400   # smaller areas are not judged
MARGIN_PX = 4       # an edge this near the area's border is the border
NEAR_PX = 3         # an edge of the candidate this near one of the solid before is not new
MIN_PX = 100        # new edge pixels inside one-colour areas, over all views, before a cut is judged
MAX_SHARE = 0.25    # ... and the share of all its new edge pixels they may be


def flat_areas(view: verify.Observed) -> np.ndarray:
    """Bool mask of the one-colour areas of a view, shrunk by MARGIN_PX. Remembered on the view."""
    known = getattr(view, "_flat_areas", None)
    if known is not None:
        return known
    labels, stats = regions.segment(view)
    tone = regions.tone_of(view.image).astype(float)
    kernel = np.ones((2 * MARGIN_PX + 1, 2 * MARGIN_PX + 1), np.uint8)
    flat = np.zeros(labels.shape, bool)
    for label in range(1, len(stats)):
        if stats[label, cv2.CC_STAT_AREA] < MIN_AREA_PX:
            continue
        x, y, w, h = (int(v) for v in stats[label, :4])
        window = labels[y:y + h, x:x + w] == label
        inner = cv2.erode(window.astype(np.uint8), kernel) > 0
        if inner.sum() < MIN_AREA_PX / 2:
            continue
        yy, xx = np.nonzero(inner)
        values = tone[y:y + h, x:x + w][inner]
        design = np.column_stack([xx, yy, np.ones(len(xx))]).astype(float)
        coef, *_ = np.linalg.lstsq(design, values, rcond=None)
        rest = values - design @ coef
        if np.percentile(rest, 95) - np.percentile(rest, 5) > RESIDUAL or np.hypot(*coef[:2]) * 100 > SLOPE:
            continue
        flat[y:y + h, x:x + w] |= inner
    view._flat_areas = flat
    return flat


class Guard:
    """Remembers the drawings of the last solids so a chain of cuts draws each solid once."""

    def __init__(self, observed: dict, dims: dict[str, float]):
        self.observed, self.dims = observed, dims
        self._drawn: list[tuple[TopoDS_Shape, dict]] = []

    def _edges(self, solid: TopoDS_Shape) -> dict[str, np.ndarray]:
        for known, edges in self._drawn:
            if known is solid:
                return edges
        deflection = 0.3 * min(view.mm_per_px for view in self.observed.values())
        mesh = verify.tessellate(solid, deflection)
        edges = {name: verify.draw(mesh, name, self.dims, view.mm_per_px).sharp
                 for name, view in self.observed.items()}
        self._drawn = [*self._drawn[-3:], (solid, edges)]
        return edges

    def new_edges(self, before: TopoDS_Shape, after: TopoDS_Shape) -> dict[str, tuple[int, int]]:
        """Per view, (pixels of new edges inside one-colour areas, pixels of new edges in all): new
        are the edges `after` draws that `before` did not."""
        old, new = self._edges(before), self._edges(after)
        kernel = np.ones((2 * NEAR_PX + 1, 2 * NEAR_PX + 1), np.uint8)
        counts = {}
        for name, view in self.observed.items():
            added = new[name] & ~(cv2.dilate(old[name].astype(np.uint8), kernel) > 0)
            counts[name] = (int((added & flat_areas(view)).sum()), int(added.sum()))
        return counts

    def refuses(self, before: TopoDS_Shape, after: TopoDS_Shape) -> str | None:
        """Why `after` may not replace `before`, or None."""
        counts = self.new_edges(before, after)
        inside = sum(c[0] for c in counts.values())
        total = sum(c[1] for c in counts.values())
        if inside <= MIN_PX or inside <= MAX_SHARE * total:
            return None
        worst = max(counts, key=lambda name: counts[name][0])
        return (f"the cut draws {inside} of its {total} px of new edges inside one-colour faces "
                f"(most in the {worst} view)")
