"""What the inputs of one case say, gathered once and shared by the steps.

Every later step used to read the views, find the lines, segment the areas and draw the solid for
itself. `Evidence` holds them for the case the agent is working on (`VisualHullAgent.evidence`):

- `observed`: mask, lines and scale of every view (verify.observe), read once;
- `drawings(solid)`: the solid drawn from the cameras of the views, kept for the last few solids;
- `regions()`: the areas between the lines of each straight view and their kind (regions.find);
- `lines(solid)`: the lines of the views that the solid does not reproduce (lines.find);
- `table(solid)`: the tone table of the renderer that made the views (tone.choose).

`rows(solid)` is the table itself: one row per region, with what the solid has under it. Nothing
here cuts, and no step decides by the flags yet: they are for ordering proposals and for a model to
read, together with the views `marked()` with the regions' numbers.

The areas of a view (regions.segment, regions._measure) are remembered on the `Observed` object
itself, so they are shared wherever the same views are passed on.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import lines as line_finder
from agentic_cad import normals, regions, shading, tone, verify

KEPT = 4             # solids whose drawings and lines are remembered
REACH_PX = 4         # a line pixel belongs to the region this near (lines are a few pixels wide)
COPLANAR_PX = 2.0    # two regions lie in one plane of the solid when their depths differ by less than this
FLAT_PX = 1.5        # ... and each is flat in the solid: its depths stay within this (8th to 92nd percentile)


class Evidence:
    def __init__(self, case_dir: Path, dims: dict[str, float]):
        self.case_dir, self.dims = case_dir, dims
        self.observed = verify.observe(case_dir, dims)
        self._regions = None
        self._table: list = []
        self._drawn: list[tuple] = []    # (solid, shading.Drawings)
        self._lines: list[tuple] = []    # (solid, {view: [lines.Piece]})
        self._rows: list[tuple] = []     # (solid, rows)

    @staticmethod
    def _known(kept: list[tuple], solid: TopoDS_Shape):
        for shape, value in kept:
            if shape is solid:
                return value
        return None

    def drawings(self, solid: TopoDS_Shape) -> "shading.Drawings":
        """The solid from the cameras of the views; each view is drawn when it is first asked for."""
        known = self._known(self._drawn, solid)
        if known is None:
            known = shading.Drawings(solid, self.observed, self.dims)
            self._drawn = [*self._drawn[-(KEPT - 1):], (solid, known)]
        return known

    def regions(self) -> dict:
        """regions.find: per straight view, the label image and the regions, largest first."""
        if self._regions is None:
            self._regions = regions.find(self.observed)
        return self._regions

    def lines(self, solid: TopoDS_Shape) -> dict:
        """lines.find: per straight view, the lines the solid does not reproduce, judged."""
        known = self._known(self._lines, solid)
        if known is None:
            known = line_finder.find(self.observed, self.drawings(solid), self.dims)
            self._lines = [*self._lines[-(KEPT - 1):], (solid, known)]
        return known

    def table(self, solid: TopoDS_Shape) -> tone.Table | None:
        """The tone table of the renderer of these views, chosen once, on the first solid asked about."""
        if not self._table:
            drawings = self.drawings(solid)
            self._table.append(tone.choose(self.observed, {name: drawings[name] for name in self.observed}))
        return self._table[0]

    # -- the table -------------------------------------------------------------------------------

    def rows(self, solid: TopoDS_Shape) -> list[dict]:
        """One row per region of the straight views: its kind by tone, and what `solid` has there.

        - depth_mm, depth_spread_mm: the solid's surface under the region, towards the camera from
          the centre of the box (median; 8th to 92nd percentile);
        - below_px: length of lines of the view, inside or along the region, that lie under the
          solid's surface (lines.py "below"): there the part has something our solid covers;
        - coplanar_with: numbers of neighbouring facing regions across a line that lie in the same
          plane of the solid. The views draw a line between two faces square to the camera only
          where they differ in depth, so one of the two is wrong;
        - tone_misfit: grey levels between the region's tone and the tone of the solid's face there;
        - iso: "facing" or "slanted" where the isometric view confirms the solid's face (normals.py)."""
        known = self._known(self._rows, solid)
        if known is not None:
            return known
        drawings, found, pieces, table = self.drawings(solid), self.regions(), self.lines(solid), self.table(solid)
        confirmed = normals.refine(found, self.observed, drawings, self.dims, table)
        result = []
        for view, (labels, _) in found.items():
            seen, drawing = self.observed[view], drawings[view]
            pixel = seen.mm_per_px
            readable = regions._readable(seen) & drawing.mask
            off = None
            if table is not None:
                off = np.abs(regions.tone_of(seen.image) - table.tone(tone.in_camera(drawing.normal, view)))
            numbers = {item.label: number for number, (item, _, _) in enumerate(confirmed[view], 1)}
            listed = np.isin(labels, list(numbers))
            # every pixel within reach of a listed region goes to the nearest one: the lines between them
            distance, nearest = cv2.distanceTransformWithLabels((~listed).astype(np.uint8), cv2.DIST_L2, 3,
                                                                labelType=cv2.DIST_LABEL_PIXEL)
            lookup = np.zeros(int(nearest.max()) + 1, np.int32)
            lookup[nearest[listed]] = labels[listed]
            grown = np.where(distance <= REACH_PX, lookup[nearest], 0)

            below = dict.fromkeys(numbers, 0.0)
            for piece in pieces.get(view, []):
                if piece.verdict != "below" or len(piece.points) < 2:
                    continue
                at = np.rint(piece.points).astype(int)
                inside = (at[:, 0] >= 0) & (at[:, 0] < grown.shape[1]) & (at[:, 1] >= 0) & (at[:, 1] < grown.shape[0])
                touched, counts = np.unique(grown[at[inside, 1], at[inside, 0]], return_counts=True)
                for label, count in zip(touched, counts):
                    if label in below:
                        below[int(label)] += piece.length * count / len(piece.points)

            view_rows = {}
            for number, (item, _, solved) in enumerate(confirmed[view], 1):
                area = labels == item.label
                depths = drawing.depth[area & drawing.mask]
                low, middle, high = np.percentile(depths, (8, 50, 92)) if len(depths) else (None, None, None)
                misfit = off[area & readable] if off is not None else np.zeros(0)
                view_rows[item.label] = {
                    "view": view, "number": number, "kind": item.kind, "area_px": item.area, "box": list(item.box),
                    "tone": item.tone, "tone_spread": item.spread,
                    "depth_mm": None if middle is None else round(float(middle), 3),
                    "depth_spread_mm": None if middle is None else round(float(high - low), 3),
                    "below_px": round(below[item.label], 1), "coplanar_with": [],
                    "tone_misfit": round(float(np.median(misfit)), 1) if len(misfit) else None,
                    "iso": solved.kind if solved is not None else None,
                }

            def flat_facing(row: dict) -> bool:
                return row["kind"] == "facing" and row["depth_mm"] is not None and row["depth_spread_mm"] <= FLAT_PX * pixel

            pairs = set()
            for first, second in ((grown[:, :-1], grown[:, 1:]), (grown[:-1, :], grown[1:, :])):
                differ = (first != second) & (first > 0) & (second > 0)
                pairs |= set(zip(first[differ].tolist(), second[differ].tolist()))
            for a, b in pairs:
                one, other = view_rows.get(a), view_rows.get(b)
                if one and other and flat_facing(one) and flat_facing(other) \
                        and abs(one["depth_mm"] - other["depth_mm"]) <= COPLANAR_PX * pixel \
                        and other["number"] not in one["coplanar_with"]:
                    one["coplanar_with"].append(other["number"])
            for row in view_rows.values():
                row["coplanar_with"].sort()
            result += sorted(view_rows.values(), key=lambda row: row["number"])
        self._rows = [*self._rows[-(KEPT - 1):], (solid, result)]
        return result

    def marked(self) -> dict[str, bytes]:
        """Every straight view as a PNG with the outlines of its regions and their numbers (the
        `number` of `rows`): what a reader needs to name a region."""
        pictures = {}
        for view, (labels, items) in self.regions().items():
            image = self.observed[view].image.copy()
            scale = max(1, image.shape[0] // 512)
            for number, item in enumerate(items, 1):
                x, y, w, h = item.box
                area = (labels[y:y + h, x:x + w] == item.label).astype(np.uint8)
                contours, _ = cv2.findContours(area, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE, offset=(x, y))
                cv2.drawContours(image, contours, -1, (200, 60, 0), scale)
                # the number goes where the region is widest, so that it stays inside it
                inner = cv2.distanceTransform(np.pad(area, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
                row, col = np.unravel_index(int(inner.argmax()), inner.shape)
                text = str(number)
                size = 0.4 * scale
                (width, height), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, size, scale)
                origin = (int(x + col - width / 2), int(y + row + height / 2))
                cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, size, (255, 255, 255), 3 * scale, cv2.LINE_AA)
                cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, size, (160, 0, 0), scale, cv2.LINE_AA)
            pictures[view] = cv2.imencode(".png", image)[1].tobytes()
        return pictures
