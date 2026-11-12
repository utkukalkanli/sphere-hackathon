"""The dark lines of the views that the solid does not reproduce. Not a step: it cuts nothing.

A line of a view marks a boundary between two faces. Where the solid has no face boundary under it,
the line runs through the inside of one face of ours, and one of two things is true:

- the surface is right there and only the boundary is missing (two faces in one plane, the seam of a
  cylinder, a face that carries on smoothly into the next): the face only has to be split;
- the part has something there that the solid lacks (the rim or the floor of a pocket, a step, a
  countersink): material has to go.

`find` lists these lines view by view as straight pieces, arcs and circles, and says for each which
of the two it is, as far as the isometric view can tell. The piece is laid on our surface and looked
up in the isometric view that sees that side. It is "level" when it lands on a line there and the
tone is the same on both sides of it, in the straight view and in the isometric one; "below" when it
lands on no line (what the straight view shows lies under our surface); "step" when the tones on its
two sides differ; "unseen" when the isometric camera does not see enough of it to say.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from agentic_cad import outline_fit as of
from agentic_cad import verify

FAR_PX = 4.0          # a line this far from every face boundary of the solid is not reproduced by it
INSIDE_PX = 3         # lines this close to the outline of the part are left out: the outline is one itself
MIN_PIECE_PX = 12     # shorter pieces are not kept
LINE_FIT_PX = 1.0     # a straight piece stays this close to its line
ARC_FIT_PX = 0.8      # an arc stays this close to its circle
MIN_SWEEP_DEG = 25.0  # a flatter arc is not told from a straight line
MERGE_PX = 8.0        # two pieces whose ends lie this close are joined when one line or circle fits both
CLOSED_PX = 3.0       # a chain whose ends are this close is a loop
SIDE_PX = (4, 6, 9, 13, 18)   # the tone beside a line is read this far from it: the first that works
EDGE_PX = 2           # ... and not this close to any line, which darkens the pixels beside it
NEAR_PX = 2.5         # a point lands on a line of the isometric view when it is this close to one
SEEN_PX = 3.0         # a point is seen by the isometric camera when our solid has nothing this far in front of it
SAME_TONE = 8         # grey levels: two tones this close are one face
MIN_SEEN = 8          # points of a piece the isometric camera must see ...
MIN_SEEN_SHARE = 0.3  # ... and their share of the piece
MIN_ON_LINE = 0.7     # share of the seen points that must land on a line for the piece to lie on our surface
MIN_SAME = 0.9        # share of the point pairs across the piece that must show one tone
MIN_PAIRS = 5

ISOMETRIC = ("iso_front_right_top", "iso_back_left_bottom")


@dataclass
class Piece:
    view: str
    kind: str                  # "line", "arc" or "circle"
    points: np.ndarray         # (n, 2) column, row along the piece, in pixels of the view
    face: int                  # the face of our solid it runs through
    centre: tuple[float, float] | None = None   # of an arc or circle, pixels
    radius: float | None = None
    verdict: str = "unseen"    # "level", "step", "below" or "unseen"
    numbers: dict = field(default_factory=dict)

    @property
    def length(self) -> float:
        return float(np.hypot(*np.diff(self.points, axis=0).T).sum())

    def summary(self) -> dict:
        ends = [[round(float(v), 1) for v in self.points[i]] for i in (0, -1)]
        return {"view": self.view, "kind": self.kind, "verdict": self.verdict, "face": self.face,
                "length_px": round(self.length, 1), "from": ends[0], "to": ends[1],
                **({"centre": [round(c, 1) for c in self.centre], "radius_px": round(self.radius, 1)}
                   if self.centre else {}), **self.numbers}


# -- which lines ---------------------------------------------------------------------------------------

def loose(observed: verify.Observed, drawing: verify.Drawing) -> np.ndarray:
    """The pixels of the view's lines that lie inside one face of the solid."""
    kernel = np.ones((2 * INSIDE_PX + 1, 2 * INSIDE_PX + 1), np.uint8)
    inside = cv2.erode((observed.mask & drawing.mask).astype(np.uint8), kernel) > 0
    distance = cv2.distanceTransform((~drawing.edges).astype(np.uint8), cv2.DIST_L2, 3)
    return observed.lines & inside & (distance > FAR_PX)


def thin(mask: np.ndarray) -> np.ndarray:
    """One pixel wide centre lines (Zhang and Suen). The lines are a few pixels wide, so a few rounds do."""
    image = np.pad(mask.astype(np.uint8), 1)
    while True:
        changed = False
        for first in (True, False):
            p2, p3, p4 = image[:-2, 1:-1], image[:-2, 2:], image[1:-1, 2:]
            p5, p6, p7 = image[2:, 2:], image[2:, 1:-1], image[2:, :-2]
            p8, p9 = image[1:-1, :-2], image[:-2, :-2]
            ring = [p2, p3, p4, p5, p6, p7, p8, p9]
            neighbours = sum(ring)
            turns = sum((ring[i] == 0) & (ring[(i + 1) % 8] == 1) for i in range(8))
            if first:
                corner = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                corner = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            remove = (image[1:-1, 1:-1] == 1) & (neighbours >= 2) & (neighbours <= 6) & (turns == 1) & corner
            if remove.any():
                image[1:-1, 1:-1][remove] = 0
                changed = True
        if not changed:
            return image[1:-1, 1:-1].astype(bool)


def chains(skeleton: np.ndarray) -> list[np.ndarray]:
    """The centre lines as runs of pixels in order, (n, 2) column, row, cut where lines meet."""
    image = skeleton.astype(np.uint8)
    neighbours = cv2.filter2D(image, cv2.CV_16S, np.ones((3, 3), np.float32), borderType=cv2.BORDER_CONSTANT) - image
    simple = skeleton & (neighbours <= 2)
    count, labels = cv2.connectedComponents(simple.astype(np.uint8), connectivity=8)
    size = skeleton.shape[0]
    result = []
    # the pixels of every run in one pass: looking each label up in the whole image is what took the time
    all_rows, all_cols = np.nonzero(labels)
    order = np.argsort(labels[all_rows, all_cols], kind="stable")
    all_rows, all_cols = all_rows[order], all_cols[order]
    ends = np.cumsum(np.bincount(labels[all_rows, all_cols], minlength=count))
    for label in range(1, count):
        rows, cols = all_rows[ends[label - 1]:ends[label]], all_cols[ends[label - 1]:ends[label]]
        if len(rows) < MIN_PIECE_PX:
            continue
        cells = set(zip(rows.tolist(), cols.tolist()))
        degree = {cell: sum((cell[0] + dr, cell[1] + dc) in cells for dr in (-1, 0, 1) for dc in (-1, 0, 1)) - 1
                  for cell in cells}
        start = min(cells, key=lambda cell: (degree[cell] != 1, cell))
        path, seen = [start], {start}
        while True:
            row, col = path[-1]
            following = [(row + dr, col + dc) for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))
                         if (row + dr, col + dc) in cells and (row + dr, col + dc) not in seen]
            if not following:
                break
            path.append(following[0])
            seen.add(following[0])
        if len(path) >= MIN_PIECE_PX and max(map(max, path)) < size:
            result.append(np.array([(col, row) for row, col in path], dtype=float))
    return result


def _fit(chain: np.ndarray) -> list[tuple]:
    """A chain as straight pieces and arcs: (kind, points, centre, radius). Split where neither fits."""
    if len(chain) < MIN_PIECE_PX:
        return []
    start, end = chain[0], chain[-1]
    closed = float(np.hypot(*(end - start))) <= CLOSED_PX and len(chain) >= 3 * MIN_PIECE_PX
    if not closed:
        along = end - start
        norm = max(float(np.hypot(*along)), 1e-9)
        off = np.abs((chain[:, 0] - start[0]) * along[1] - (chain[:, 1] - start[1]) * along[0]) / norm
        if off.max() <= LINE_FIT_PX:
            return [("line", chain, None, None)]
    else:
        off = np.hypot(*(chain - chain.mean(axis=0)).T)  # only used to choose where to split
    centre, radius = of.fit_circle(chain)
    if np.isfinite(radius) and radius < 4 * len(chain):
        distance = np.abs(np.hypot(*(chain - centre).T) - radius)
        angles = np.unwrap(np.arctan2(chain[:, 1] - centre[1], chain[:, 0] - centre[0]))
        sweep = abs(float(np.degrees(angles[-1] - angles[0])))
        if distance.max() <= 2 * ARC_FIT_PX and np.sqrt((distance ** 2).mean()) <= ARC_FIT_PX and sweep >= MIN_SWEEP_DEG:
            kind = "circle" if closed and sweep >= 330 else "arc"
            return [(kind, chain, (float(centre[0]), float(centre[1])), float(radius))]
    cut = int(np.argmax(off)) if not closed else len(chain) // 2
    cut = min(max(cut, 2), len(chain) - 3)
    return _fit(chain[:cut + 1]) + _fit(chain[cut:])


def _single(chain: np.ndarray) -> tuple | None:
    """The chain as one straight piece, arc or circle, or None when it is neither."""
    start, end = chain[0], chain[-1]
    closed = float(np.hypot(*(end - start))) <= MERGE_PX and len(chain) >= 3 * MIN_PIECE_PX
    if not closed:
        along = end - start
        norm = max(float(np.hypot(*along)), 1e-9)
        off = np.abs((chain[:, 0] - start[0]) * along[1] - (chain[:, 1] - start[1]) * along[0]) / norm
        if off.max() <= LINE_FIT_PX:
            return "line", chain, None, None
    centre, radius = of.fit_circle(chain)
    if not np.isfinite(radius) or radius >= 4 * len(chain):
        return None
    distance = np.abs(np.hypot(*(chain - centre).T) - radius)
    angles = np.unwrap(np.arctan2(chain[:, 1] - centre[1], chain[:, 0] - centre[0]))
    sweep = abs(float(np.degrees(angles[-1] - angles[0])))
    if distance.max() > 2 * ARC_FIT_PX or np.sqrt((distance ** 2).mean()) > ARC_FIT_PX or sweep < MIN_SWEEP_DEG:
        return None
    return ("circle" if closed and sweep >= 330 else "arc"), chain, (float(centre[0]), float(centre[1])), float(radius)


def merge(fitted: list[tuple]) -> list[tuple]:
    """Join pieces that end near each other and lie on one line or one circle. A long arc is found
    as several short pieces, each too flat to be told from a straight line, with gaps where other
    lines cross it."""
    fitted = list(fitted)
    changed = True
    while changed:
        changed = False
        for i in range(len(fitted)):
            for j in range(i + 1, len(fitted)):
                a, b = fitted[i][1], fitted[j][1]
                for first, second in ((a, b), (a, b[::-1]), (a[::-1], b), (a[::-1], b[::-1])):
                    if np.hypot(*(first[-1] - second[0])) > MERGE_PX:
                        continue
                    one = _single(np.vstack([first, second]))
                    if one is not None:
                        fitted[i] = one
                        del fitted[j]
                        changed = True
                        break
                if changed:
                    break
            if changed:
                break
    return fitted


# -- between the views ---------------------------------------------------------------------------------

def lift(view: str, points: np.ndarray, depth: np.ndarray, dims: dict, mm_per_px: float, size: int) -> np.ndarray:
    """Pixels (column, row) of a view at a depth (mm towards its camera) as points in mm from the box centre."""
    right, up, towards = verify.camera(view)
    half = size / 2
    return ((points[:, 0] - half + 0.5) * mm_per_px)[:, None] * right \
        + ((half - 0.5 - points[:, 1]) * mm_per_px)[:, None] * up + depth[:, None] * towards


def land(view: str, points: np.ndarray, mm_per_px: float, size: int) -> tuple[np.ndarray, np.ndarray]:
    """Where points (mm from the box centre) appear in a view: pixels (column, row) and depth."""
    right, up, towards = verify.camera(view)
    half = size / 2
    pixels = np.stack([points @ right / mm_per_px + half - 0.5, half - 0.5 - points @ up / mm_per_px], axis=1)
    return pixels, points @ towards


def iso_for(view: str) -> str | None:
    """The isometric view that sees the side a straight camera looks at."""
    if view in ISOMETRIC:
        return None
    towards = verify.camera(view)[2]
    return max(ISOMETRIC, key=lambda iso: verify.camera(iso)[2] @ towards)


def _at(image: np.ndarray, pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Values of an image at (column, row) positions, and which positions lie inside it."""
    cols, rows = np.rint(pixels[:, 0]).astype(int), np.rint(pixels[:, 1]).astype(int)
    inside = (rows >= 0) & (cols >= 0) & (rows < image.shape[0]) & (cols < image.shape[1])
    return image[np.where(inside, rows, 0), np.where(inside, cols, 0)], inside


def _normals(piece: Piece) -> np.ndarray:
    """Unit vectors across the piece at each of its points, in pixels."""
    if piece.centre is not None:
        across = piece.points - np.array(piece.centre)
    else:
        along = piece.points[-1] - piece.points[0]
        across = np.tile([-along[1], along[0]], (len(piece.points), 1))
    return across / np.maximum(np.hypot(*across.T), 1e-9)[:, None]


class Judge:
    """Says what a piece of one straight view is, from the isometric view that sees that side."""

    def __init__(self, view: str, observed: dict, drawings, dims: dict):
        self.view, self.dims = view, dims
        self.here, self.drawing = observed[view], drawings[view]
        self.size = self.here.mask.shape[0]
        self.tone = self.here.image.max(axis=2).astype(np.int16)
        kernel = np.ones((2 * EDGE_PX + 1, 2 * EDGE_PX + 1), np.uint8)
        self.clear = ~(cv2.dilate(self.here.lines.astype(np.uint8), kernel) > 0)
        iso = iso_for(view)
        self.iso = observed.get(iso) if iso else None
        if self.iso is not None:
            self.iso_drawing = drawings[iso]
            self.iso_tone = self.iso.image.max(axis=2).astype(np.int16)
            self.iso_distance = cv2.distanceTransform((~self.iso.lines).astype(np.uint8), cv2.DIST_L2, 3)

    def _in_iso(self, pixels: np.ndarray):
        """Points of this view on our surface, in the isometric view: pixels there, and which are seen."""
        depth, inside = _at(self.drawing.depth, pixels)
        usable = inside & np.isfinite(depth)
        points = lift(self.view, pixels, np.where(usable, depth, 0.0), self.dims, self.here.mm_per_px, self.size)
        landed, distance = land(self.iso.name, points, self.iso.mm_per_px, self.iso.mask.shape[0])
        surface, within = _at(self.iso_drawing.depth, landed)
        seen = usable & within & np.isfinite(surface) & (surface - distance <= SEEN_PX * self.iso.mm_per_px)
        return landed, seen

    def judge(self, piece: Piece) -> None:
        if self.iso is None:
            return
        landed, seen = self._in_iso(piece.points)
        piece.numbers["seen"] = round(float(seen.mean()), 2)
        if seen.sum() < MIN_SEEN or seen.mean() < MIN_SEEN_SHARE:
            return
        near, _ = _at(self.iso_distance, landed[seen])
        on_line = float((near <= NEAR_PX).mean())
        piece.numbers["on_line"] = round(on_line, 2)
        if on_line < MIN_ON_LINE:
            piece.verdict = "below"
            return
        # The tone beside the line, on both sides. The isometric view can show the same stretch much
        # shorter than this view does (and at a coarser scale), so the distance is raised until the points
        # clear the line there too.
        coarser = max(1.0, self.iso.mm_per_px / self.here.mm_per_px)
        for distance in SIDE_PX:
            across = _normals(piece) * distance * coarser
            sides, usable = [], np.ones(len(piece.points), bool)
            for sign in (1, -1):
                pixels = piece.points + sign * across
                face, inside = _at(self.drawing.face, pixels)
                clear, _ = _at(self.clear, pixels)
                tone, _ = _at(self.tone, pixels)
                there, seen_there = self._in_iso(pixels)
                iso_tone, _ = _at(self.iso_tone, there)
                gap, _ = _at(self.iso_distance, there)
                usable &= inside & (face == piece.face) & clear & seen_there & (gap > EDGE_PX)
                sides.append((tone, iso_tone))
            if usable.sum() >= max(MIN_PAIRS, MIN_SEEN_SHARE * len(usable)):
                break
        piece.numbers["pairs"] = int(usable.sum())
        if usable.sum() < MIN_PAIRS:
            return
        same_here = float((np.abs(sides[0][0] - sides[1][0])[usable] <= SAME_TONE).mean())
        same_iso = float((np.abs(sides[0][1] - sides[1][1])[usable] <= SAME_TONE).mean())
        piece.numbers.update(same_here=round(same_here, 2), same_iso=round(same_iso, 2))
        piece.verdict = "level" if min(same_here, same_iso) >= MIN_SAME else "step"


# -- all of it -----------------------------------------------------------------------------------------

def pieces_of(view: str, observed: verify.Observed, drawing: verify.Drawing) -> list[Piece]:
    """The lines of one view that the solid does not reproduce, as fitted pieces, not yet judged."""
    result = []
    fitted = [piece for chain in chains(thin(loose(observed, drawing))) for piece in _fit(chain)]
    if True:
        for kind, points, centre, radius in merge(fitted):
            faces, inside = _at(drawing.face, points)
            faces = faces[inside & (faces >= 0)]
            if len(faces) == 0:
                continue
            values, counts = np.unique(faces, return_counts=True)
            result.append(Piece(view, kind, points, int(values[np.argmax(counts)]), centre, radius))
    return result


def find(observed: dict[str, verify.Observed], drawings, dims: dict[str, float],
         views: tuple[str, ...] = verify.ORTHO_VIEWS) -> dict[str, list[Piece]]:
    """Every line of the given views that the solid does not reproduce. `drawings[name]` is the solid
    drawn from that view's camera (`shading.Drawings`)."""
    found = {}
    for view in views:
        if view not in observed:
            continue
        pieces = pieces_of(view, observed[view], drawings[view])
        judge = Judge(view, observed, drawings, dims)
        for piece in pieces:
            judge.judge(piece)
        found[view] = pieces
    return found
