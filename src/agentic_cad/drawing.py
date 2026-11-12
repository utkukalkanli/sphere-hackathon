"""The line drawing of a view: its faces as closed areas and its edges as lines and arcs.

A view is a shaded picture with a dark line on every face boundary. Finding the line pixels is
not enough to work on: a line that fades for a few pixels lets two faces run into each other, and
pixels are not edges. So the picture is first divided into faces, and the edges are then the
boundaries of that division, which are closed by construction:

1. `edge_pixels`: where the picture has an edge. A line is darker than what a small closing fills
   in, by a fixed contrast or, on a dark face, by a share of the face's own tone; where a bright
   face meets a dark one the jump in tone is the edge.
2. `partition`: the part's pixels that are not edge pixels, shrunk by a pixel so that a gap in a
   line closes, fall into the cores of the faces. The cores are grown back over the picture
   (watershed): they meet on the lines, and across a gap along the shortest way. A face too thin
   to have a core keeps its own area. Two areas whose common border is mostly not on edge pixels
   were one face with a narrow neck and are joined again.
3. `strokes`: the border between two faces, followed from one meeting point of three faces to the
   next, moved onto the middle of the line to a fraction of a pixel, and replaced by the straight
   lines and circular arcs that fit it. Ends that meet are put on one point.
4. `loose`: the lines that divide nothing (the seam of a cylinder, a line that ends inside a
   face), fitted the same way.

Everything is in pixels of the view, (column, row), pixel centres at whole numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from agentic_cad import lines as between
from agentic_cad import outline_fit, verify

LINE_CONTRAST = verify.LINE_CONTRAST   # a line is this much darker than its surroundings
FAINT_MIN = 6          # ... or, on a dark face, at least this much
FAINT_SHARE = 0.10     # ... and this share of the tone around it, where it hangs on a clearer line
CLEAR_SHARE = 0.22     # ... or this share by itself
TONE_JUMP = 24         # neighbouring pixels this far apart in tone lie on two faces
MIN_THIN_PX = 6        # a face without a core is kept from this many pixels
MIN_ON_EDGE = 0.5      # share of a border between two areas that must lie on edge pixels, or they are one face
LINE_PX = 0.6          # how far a fitted line may be from the border it replaces
ARC_PX = 0.6           # ... and an arc
MIN_ARC_POINTS = 8     # an arc is fitted to at least this many points
MIN_SWEEP = math.radians(10)   # ... and turns at least this far
MAX_RADIUS = 4000.0    # a flatter arc is a line
AXIS_DRIFT_PX = 0.75   # a line that drifts less than this over its length is exactly horizontal or vertical
JOIN_PX = 2.5          # ends this close are one point
REFINE_PX = 2          # a border point is moved onto the line within this many pixels
MIN_LOOSE_PX = 12      # shorter lines that divide nothing are left out
LONG_LINE_PX = 30.0    # a straight line at least this long is an edge by itself
MAX_TURN = math.radians(30)    # pieces that meet at a flatter angle run on as one curve
SMOOTH = 5             # border points a curve is averaged over: takes the staircase out
MIN_TURN = math.radians(1.5)   # ... and at a flatter one they are one straight line, not a curve


@dataclass
class Stroke:
    kind: str                  # "line", "arc", "circle" or "curve"
    points: np.ndarray         # (n, 2) the stroke as a polyline, for drawing and measuring
    faces: tuple[int, int] = (0, 0)   # the two areas it divides (0: outside the part); equal for a loose line
    centre: tuple[float, float] | None = None   # arcs and circles
    radius: float | None = None

    @property
    def length(self) -> float:
        return float(np.linalg.norm(np.diff(self.points, axis=0), axis=1).sum())


@dataclass
class Border:
    """The border between two faces from one meeting point to the next: one edge of the picture."""
    faces: tuple[int, int]     # the two areas it divides (0: outside the part)
    strokes: list[Stroke]      # in order, each ending where the next begins
    closed: bool = False       # a loop: it has no ends
    front: int | None = None   # the face that hides the other along this edge, where the picture says so
    why: str = ""              # what said so

    @property
    def length(self) -> float:
        return sum(stroke.length for stroke in self.strokes)

    def points(self) -> np.ndarray:
        return np.vstack([stroke.points for stroke in self.strokes])

    def leaving(self, end: int, reach: float = 8.0) -> np.ndarray:
        """The direction in which the border leaves its first (0) or last (-1) point, over `reach` pixels."""
        points = self.points() if end == 0 else self.points()[::-1]
        away = np.linalg.norm(points - points[0], axis=1)
        far = points[min(int(np.searchsorted(np.maximum.accumulate(away), reach)), len(points) - 1)]
        step = far - points[0]
        norm = np.linalg.norm(step)
        return step / norm if norm > 0 else np.zeros(2)


@dataclass
class Drawing:
    labels: np.ndarray                     # int32, 0 outside the part, 1.. one value per face
    strokes: list[Stroke] = field(default_factory=list)   # the borders of the faces, piece by piece
    loose: list[Stroke] = field(default_factory=list)     # lines that divide nothing
    borders: list[Border] = field(default_factory=list)   # the same borders, edge by edge

    @property
    def faces(self) -> int:
        return int(self.labels.max())

    def counts(self) -> dict[str, int]:
        result = {"faces": self.faces, "loose": len(self.loose)}
        for stroke in self.strokes:
            result[stroke.kind] = result.get(stroke.kind, 0) + 1
        return result


# -- 1. where the picture has an edge -----------------------------------------------------------------

def darkness(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How much darker each pixel is than what a small closing fills in, and the closed tone."""
    bright = image.max(axis=2)
    closed = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)).astype(np.float32)
    return closed - bright, closed


def jumps(image: np.ndarray) -> np.ndarray:
    """Per pixel the largest difference in tone to a 4-neighbour."""
    tones = image.max(axis=2).astype(np.float32)
    result = np.zeros(tones.shape, np.float32)
    across, down = np.abs(np.diff(tones, axis=1)), np.abs(np.diff(tones, axis=0))
    result[:, :-1] = np.maximum(result[:, :-1], across)
    result[:, 1:] = np.maximum(result[:, 1:], across)
    result[:-1, :] = np.maximum(result[:-1, :], down)
    result[1:, :] = np.maximum(result[1:, :], down)
    return result


def edge_pixels(seen: verify.Observed) -> np.ndarray:
    """The pixels of the part that lie on an edge of the picture."""
    darker, closed = darkness(seen.image)
    clear = (darker >= LINE_CONTRAST) | (darker >= np.maximum(FAINT_MIN, CLEAR_SHARE * closed))
    faint = (darker >= np.maximum(FAINT_MIN, FAINT_SHARE * closed)).astype(np.uint8)
    count, labels = cv2.connectedComponents(faint, connectivity=8)
    hangs = np.zeros(count, bool)
    hangs[np.unique(labels[clear])] = True
    hangs[0] = False
    tones = seen.image.max(axis=2).astype(np.int16)
    step = np.zeros(tones.shape, bool)   # the darker pixel of a jump: that is where the renderer's line would be
    for axis in (0, 1):
        first = (slice(None, -1), slice(None)) if axis == 0 else (slice(None), slice(None, -1))
        second = (slice(1, None), slice(None)) if axis == 0 else (slice(None), slice(1, None))
        difference = tones[first] - tones[second]
        step[first] |= difference <= -TONE_JUMP
        step[second] |= difference >= TONE_JUMP
    return (hangs[labels] | step) & seen.mask


# -- 2. the faces --------------------------------------------------------------------------------------

def partition(seen: verify.Observed, edges: np.ndarray | None = None) -> np.ndarray:
    """A face number for every pixel of the part (0 outside)."""
    mask = seen.mask
    edges = edge_pixels(seen) if edges is None else edges
    free = (mask & ~edges).astype(np.uint8)
    square = np.ones((3, 3), np.uint8)
    core = cv2.erode(free, square)
    cores, core_labels = cv2.connectedComponents(core, connectivity=4)
    count, free_labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    with_core = np.zeros(count, bool)
    with_core[np.unique(free_labels[core > 0])] = True
    thin = ~with_core & (stats[:, cv2.CC_STAT_AREA] >= MIN_THIN_PX)
    thin[0] = False
    number = np.zeros(count, np.int32)
    number[thin] = cores + 1 + np.arange(int(thin.sum()))   # 1 is the background, 2.. the cores, then the thin faces

    markers = np.zeros(mask.shape, np.int32)
    markers[cv2.dilate(mask.astype(np.uint8), square) == 0] = 1
    markers[core > 0] = core_labels[core > 0] + 1
    markers[thin[free_labels]] = number[free_labels][thin[free_labels]]
    cv2.watershed(np.ascontiguousarray(seen.image[:, :, :3]), markers)

    # two areas whose border is mostly not on an edge are one face with a narrow neck
    areas = np.where(markers > 1, markers, 0).astype(np.float32)
    highest = cv2.dilate(areas, square)
    lowest = cv2.erode(np.where(areas > 0, areas, np.float32(1e9)), square)
    border = (markers == -1) & mask & (lowest < highest) & (lowest < 1e9)
    parent = np.arange(int(markers.max()) + 1)

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    if border.any():
        near_edge = cv2.dilate(edges.astype(np.uint8), square) > 0
        low, high = lowest[border].astype(np.int64), highest[border].astype(np.int64)
        pairs, where = np.unique(low * (len(parent) + 1) + high, return_inverse=True)
        total = np.bincount(where, minlength=len(pairs))
        on_edge = np.bincount(where, weights=near_edge[border], minlength=len(pairs))
        for pair, pixels, lined in zip(pairs, total, on_edge):
            if lined < MIN_ON_EDGE * pixels:
                a, b = root(int(pair // (len(parent) + 1))), root(int(pair % (len(parent) + 1)))
                if a != b:
                    parent[max(a, b)] = min(a, b)
    roots = np.array([root(i) for i in range(len(parent))])
    merged = np.where(markers > 1, roots[np.maximum(markers, 0)], 0)

    # the pixels on the lines go to the nearest face
    known = merged > 0
    if not known.any():
        return mask.astype(np.int32)
    _, nearest = cv2.distanceTransformWithLabels((~known).astype(np.uint8), cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
    lookup = np.zeros(int(nearest.max()) + 1, np.int32)
    lookup[nearest[known]] = merged[known]
    filled = np.where(mask, lookup[nearest], 0)
    values, compact = np.unique(filled, return_inverse=True)   # 0 stays 0: it is the smallest
    result = compact.reshape(filled.shape).astype(np.int32)
    return result if values[0] == 0 else result + 1


# -- 3. the borders as lines and arcs ------------------------------------------------------------------

def _borders(labels: np.ndarray) -> list[tuple[int, int, np.ndarray, bool]]:
    """The border between every two neighbouring areas as runs of pixels in order:
    (area, the area on the other side, (n, 2) points on this area's side, closed)."""
    padded = np.pad(labels, 1)
    rows, cols = np.nonzero(labels)
    count = int(labels.max()) + 1
    top = np.full(count, labels.shape[0])
    left = np.full(count, labels.shape[1])
    bottom, right = np.zeros(count, int), np.zeros(count, int)
    np.minimum.at(top, labels[rows, cols], rows)
    np.minimum.at(left, labels[rows, cols], cols)
    np.maximum.at(bottom, labels[rows, cols], rows)
    np.maximum.at(right, labels[rows, cols], cols)
    result = []
    for label in range(1, count):
        if bottom[label] < top[label]:
            continue
        window = (labels[top[label]:bottom[label] + 1, left[label]:right[label] + 1] == label).astype(np.uint8)
        contours, _ = cv2.findContours(window, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE, offset=(int(left[label]), int(top[label])))
        for contour in contours:
            points = contour.reshape(-1, 2)
            if len(points) < 3:
                continue
            other = np.full(len(points), -1)
            for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, 1), (-1, -1), (1, -1)):
                beside = padded[points[:, 1] + 1 + dy, points[:, 0] + 1 + dx]
                take = (other < 0) & (beside != label)
                other[take] = beside[take]
            if (other < 0).all():
                continue
            for index in np.nonzero(other < 0)[0]:   # an inner pixel of a one-pixel bridge: as its neighbour
                other[index] = other[index - 1]
            change = np.nonzero(other != np.roll(other, 1))[0]
            if len(change) == 0:
                if other[0] == 0 or label < other[0]:
                    result.append((label, int(other[0]), points.astype(float), True))
                continue
            points, other = np.roll(points, -change[0], axis=0), np.roll(other, -change[0])
            starts = np.nonzero(other != np.roll(other, 1))[0]
            ends = np.append(starts[1:], len(points))
            for start, end in zip(starts, ends):
                if other[start] != 0 and label > other[start]:
                    continue   # drawn from the other side
                stop = min(end + 1, len(points))   # up to the first point of the next run: the runs meet
                run = points[start:stop] if end < len(points) else np.vstack([points[start:], points[:1]])
                if len(run) >= 2:
                    result.append((label, int(other[start]), run.astype(float), False))
    return result


def _refine(points: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """Border points moved onto the middle of the line they run along: the centre of the edge
    strength around each, to a fraction of a pixel."""
    size = weight.shape[0]
    reach = np.arange(-REFINE_PX, REFINE_PX + 1)
    dx, dy = np.meshgrid(reach, reach)
    dx, dy = dx.ravel(), dy.ravel()
    cols = np.clip(np.rint(points[:, 0]).astype(int)[:, None] + dx, 0, size - 1)
    rows = np.clip(np.rint(points[:, 1]).astype(int)[:, None] + dy, 0, size - 1)
    heavy = weight[rows, cols] ** 2
    total = heavy.sum(axis=1)
    moved = points.copy()
    ok = total > 0
    moved[ok, 0] = (heavy[ok] * cols[ok]).sum(axis=1) / total[ok]
    moved[ok, 1] = (heavy[ok] * rows[ok]).sum(axis=1) / total[ok]
    return moved


def _line_through(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Least-squares line: a point on it, its direction, and the largest distance of a point from it."""
    centre = points.sum(axis=0) / len(points)
    dx, dy = points[:, 0] - centre[0], points[:, 1] - centre[1]
    # the main axis of the scatter, in closed form (this is asked tens of thousands of times a view)
    angle = 0.5 * math.atan2(2.0 * float(dx @ dy), float(dx @ dx) - float(dy @ dy))
    across, along = math.sin(angle), math.cos(angle)
    return centre, np.array([along, across]), float(np.abs(dy * along - dx * across).max())


SINGULAR = 1e-13       # normal equations this close to singular are left to the general solver


def _circle_through(points: np.ndarray) -> tuple[np.ndarray, float]:
    """`outline_fit.fit_circle` for the many short runs tried here: the same algebraic start and
    the same geometric steps, solved in closed form (about four times as fast as a general
    least-squares solve per step) and stopped once a step no longer moves the circle. Points so
    close to one line that this is not exact go to `outline_fit.fit_circle` itself."""
    count = len(points)
    middle_x, middle_y = float(points[:, 0].sum()) / count, float(points[:, 1].sum()) / count
    x, y = points[:, 0] - middle_x, points[:, 1] - middle_y
    z = x * x + y * y
    xx, yy, xy = float(x @ x), float(y @ y), float(x @ y)
    det = xx * yy - xy * xy
    if not det > SINGULAR * (xx + yy) ** 2:
        return outline_fit.fit_circle(points)
    xz, yz = float(x @ z), float(y @ z)
    cx, cy = (xz * yy - yz * xy) / (2 * det), (yz * xx - xz * xy) / (2 * det)
    radius = math.sqrt(max(float(z.sum()) / count + cx * cx + cy * cy, 1e-12))
    for _ in range(6):
        ox, oy = x - cx, y - cy
        distance = np.maximum(np.hypot(ox, oy), 1e-12)
        u, v, error = ox / distance, oy / distance, distance - radius
        # a step (a, b) of the centre and r of the radius solve u a + v b + r = error in the least-squares
        # sense; r follows from the means, a and b from the parts of u and v that vary
        mean_u, mean_v, mean_error = float(u.sum()) / count, float(v.sum()) / count, float(error.sum()) / count
        u, v, error = u - mean_u, v - mean_v, error - mean_error
        uu, vv, uv = float(u @ u), float(v @ v), float(u @ v)
        det = uu * vv - uv * uv
        if not det > SINGULAR * (uu + vv) ** 2:
            return outline_fit.fit_circle(points)
        ue, ve = float(u @ error), float(v @ error)
        a, b = (ue * vv - ve * uv) / det, (ve * uu - ue * uv) / det
        r = mean_error - a * mean_u - b * mean_v
        if not (math.isfinite(a) and math.isfinite(b) and math.isfinite(r)):
            break
        cx, cy, radius = cx + a, cy + b, radius + r
        if abs(a) + abs(b) + abs(r) < 1e-11:
            break
    return np.array([cx + middle_x, cy + middle_y]), abs(radius)


def _arc_through(points: np.ndarray) -> tuple[np.ndarray, float, float] | None:
    """Least-squares circle through points that turn far enough: centre, radius, largest distance."""
    if len(points) < MIN_ARC_POINTS:
        return None
    try:
        centre, radius = _circle_through(points)
    except Exception:
        return None
    if not np.isfinite(radius) or not 2.0 <= radius <= MAX_RADIUS:
        return None
    offset = points - centre
    angles = np.unwrap(np.arctan2(offset[:, 1], offset[:, 0]))
    if abs(angles[-1] - angles[0]) < MIN_SWEEP:
        return None
    return centre, radius, float(np.abs(np.hypot(offset[:, 0], offset[:, 1]) - radius).max())


def _furthest(points: np.ndarray, start: int, fits) -> int:
    """The last index up to which points[start:] still fit, found by doubling and halving."""
    last = len(points) - 1
    good, step = start + 1, 2
    while good < last:
        trial = min(last, start + step)
        if fits(points[start:trial + 1]):
            good, step = trial, step * 2
            if trial == last:
                return last
        else:
            low, high = good, trial
            while high - low > 1:
                middle = (low + high) // 2
                if fits(points[start:middle + 1]):
                    low = middle
                else:
                    high = middle
            return low
    return good


def _sample_arc(centre: np.ndarray, radius: float, first: np.ndarray, last: np.ndarray, through: np.ndarray) -> np.ndarray:
    """An arc from `first` to `last` on the side `through` lies, as a polyline fine enough to look round."""
    start = math.atan2(first[1] - centre[1], first[0] - centre[0])
    middle = math.atan2(through[1] - centre[1], through[0] - centre[0])
    end = math.atan2(last[1] - centre[1], last[0] - centre[0])
    sweep = (end - start) % (2 * math.pi)
    if (middle - start) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    steps = max(2, int(abs(sweep) * radius / 1.5) + 1)
    angles = start + np.linspace(0, sweep, steps + 1)
    return np.stack([centre[0] + radius * np.cos(angles), centre[1] + radius * np.sin(angles)], axis=1)


def fit(points: np.ndarray, closed: bool = False) -> list[Stroke]:
    """A run of border points as the fewest straight lines and circular arcs that stay within a
    fraction of a pixel of it. What is neither stays a short line."""
    if closed and len(points) >= MIN_ARC_POINTS:
        loop = _arc_through(np.vstack([points, points[:1]])[: len(points) // 2 + 2])
        whole = None
        try:
            centre, radius = outline_fit.fit_circle(points)
            distance = np.abs(np.hypot(*(points - centre).T) - radius)
            whole = (centre, radius) if distance.max() <= ARC_PX and radius >= 2.0 else None
        except Exception:
            whole = None
        if whole is not None and loop is not None:
            angles = np.linspace(0, 2 * math.pi, max(24, int(2 * math.pi * whole[1] / 1.5)) + 1)
            ring = np.stack([whole[0][0] + whole[1] * np.cos(angles), whole[0][1] + whole[1] * np.sin(angles)], axis=1)
            return [Stroke("circle", ring, centre=tuple(map(float, whole[0])), radius=float(whole[1]))]
        points = np.vstack([points, points[:1]])
    strokes, spans, start = [], [], 0
    while start < len(points) - 1:
        straight = _furthest(points, start, lambda run: _line_through(run)[2] <= LINE_PX)
        curved = start
        if len(points) - start >= MIN_ARC_POINTS:
            def round_enough(run: np.ndarray) -> bool:
                found = _arc_through(run)
                return found is not None and found[2] <= ARC_PX
            if round_enough(points[start:start + MIN_ARC_POINTS]):
                curved = _furthest_from(points, start, round_enough)
        if curved > straight + max(3, 0.25 * (straight - start)):
            run = points[start:curved + 1]
            centre, radius, _ = _arc_through(run)
            first = centre + (run[0] - centre) / np.linalg.norm(run[0] - centre) * radius
            last = centre + (run[-1] - centre) / np.linalg.norm(run[-1] - centre) * radius
            strokes.append(Stroke("arc", _sample_arc(centre, radius, first, last, run[len(run) // 2]),
                                  centre=tuple(map(float, centre)), radius=float(radius)))
            spans.append((start, curved))
            start = curved
        else:
            run = points[start:straight + 1]
            centre, direction, _ = _line_through(run)
            ends = np.array([centre + ((run[0] - centre) @ direction) * direction,
                             centre + ((run[-1] - centre) @ direction) * direction])
            drift = np.abs(ends[1] - ends[0])
            if drift[0] <= AXIS_DRIFT_PX < drift[1]:
                ends[:, 0] = run[:, 0].mean()      # exactly vertical
            elif drift[1] <= AXIS_DRIFT_PX < drift[0]:
                ends[:, 1] = run[:, 1].mean()      # exactly horizontal
            strokes.append(Stroke("line", ends))
            spans.append((start, straight))
            start = straight
    strokes = _curves(points, strokes, spans)
    # consecutive strokes meet in one point: where two lines cross, else half way
    for one, other in zip(strokes, strokes[1:]):
        meet = (one.points[-1] + other.points[0]) / 2
        if one.kind == "line" and other.kind == "line":
            a, b = one.points[-1] - one.points[0], other.points[-1] - other.points[0]
            cross = a[0] * b[1] - a[1] * b[0]
            if abs(cross) > 0.17 * np.linalg.norm(a) * np.linalg.norm(b):   # more than 10 degrees apart
                along = ((other.points[0] - one.points[0])[0] * b[1] - (other.points[0] - one.points[0])[1] * b[0]) / cross
                crossing = one.points[0] + along * a
                if np.linalg.norm(crossing - meet) <= 2 * JOIN_PX:
                    meet = crossing
        one.points[-1], other.points[0] = meet, meet
    return strokes


def _curves(points: np.ndarray, strokes: list[Stroke], spans: list[tuple[int, int]]) -> list[Stroke]:
    """A border that is neither straight nor circular (an ellipse: a circle seen at an angle) comes
    out of the fit as a row of short chords. Short pieces that run on into each other without a
    corner are put back together as one smooth curve through the border points."""
    def short(index: int) -> bool:
        return strokes[index].kind == "arc" or strokes[index].length < LONG_LINE_PX

    def turn(one: Stroke, other: Stroke) -> float:
        """The angle between two pieces where they meet, signed by the way it turns."""
        a, b = one.points[-1] - one.points[-2], other.points[1] - other.points[0]
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm == 0:
            return math.pi
        return math.copysign(math.acos(float(np.clip(a @ b / norm, -1, 1))), a[0] * b[1] - a[1] * b[0])

    result, index = [], 0
    while index < len(strokes):
        # pieces that run on into each other, all turning the same way
        last, way = index, 0.0
        while last + 1 < len(strokes):
            angle = turn(strokes[last], strokes[last + 1])
            if abs(angle) > MAX_TURN or abs(angle) < MIN_TURN or way * angle < 0:
                break
            way, last = angle, last + 1
        members = last - index + 1
        if members == 2 and not (short(index) and short(last)):
            last = index   # two long lines at a flat angle are two edges
        chords = sum(1 for k in range(index, last + 1) if strokes[k].kind == "line")
        if last > index and (chords >= 2 or last - index >= 2):
            run = points[spans[index][0]:spans[last][1] + 1]
            if len(run) >= 2 * SMOOTH:
                padded = np.concatenate([np.repeat(run[:1], SMOOTH, axis=0), run, np.repeat(run[-1:], SMOOTH, axis=0)])
                kernel = np.ones(SMOOTH) / SMOOTH
                smooth = np.stack([np.convolve(padded[:, k], kernel, mode="same") for k in (0, 1)], axis=1)[SMOOTH:-SMOOTH]
                smooth[0], smooth[-1] = run[0], run[-1]
                run = cv2.approxPolyDP(smooth.astype(np.float32).reshape(-1, 1, 2), 0.2, False).reshape(-1, 2).astype(float)
            result.append(Stroke("curve", np.array(run, dtype=float)))
        else:
            result.extend(strokes[index:last + 1])
        index = last + 1
    return result


def _furthest_from(points: np.ndarray, start: int, fits) -> int:
    """As `_furthest`, for a fit that needs MIN_ARC_POINTS points to say anything."""
    last = len(points) - 1
    good = start + MIN_ARC_POINTS - 1
    step = 2 * MIN_ARC_POINTS
    while good < last:
        trial = min(last, start + step)
        if fits(points[start:trial + 1]):
            good, step = trial, step * 2
        else:
            low, high = good, trial
            while high - low > 1:
                middle = (low + high) // 2
                if fits(points[start:middle + 1]):
                    low = middle
                else:
                    high = middle
            return low
    return good


def _join(strokes: list[list[Stroke]]) -> None:
    """Ends of border runs that lie within JOIN_PX of each other are put on their common centre."""
    ends = []
    for run in strokes:
        if run and run[0].kind != "circle":
            ends.append((run[0], 0))
            ends.append((run[-1], -1))
    if not ends:
        return
    where = np.array([stroke.points[index] for stroke, index in ends])
    cells: dict[tuple[int, int], list[int]] = {}
    for number, point in enumerate(where):
        cells.setdefault((int(point[0] // JOIN_PX), int(point[1] // JOIN_PX)), []).append(number)
    group = np.arange(len(ends))

    def root(index: int) -> int:
        while group[index] != index:
            group[index] = group[group[index]]
            index = group[index]
        return index

    for (cx, cy), members in cells.items():
        near = [n for ox in (-1, 0, 1) for oy in (-1, 0, 1) for n in cells.get((cx + ox, cy + oy), [])]
        for a in members:
            for b in near:
                if a < b and np.linalg.norm(where[a] - where[b]) <= JOIN_PX:
                    group[root(b)] = root(a)
    roots = np.array([root(i) for i in range(len(ends))])
    for value in np.unique(roots):
        members = np.nonzero(roots == value)[0]
        if len(members) > 1:
            centre = where[members].mean(axis=0)
            for member in members:
                stroke, index = ends[member]
                stroke.points[index] = centre


# -- 5. which face hides which -------------------------------------------------------------------------

T_BAR = math.radians(12)     # two borders that leave a meeting point this close to opposite ways run through it
T_STEM = math.radians(25)    # ... and the third must leave at least this far off their line


def meetings(drawing: Drawing) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """The points where borders end, each with the borders that end there as (index, 0 or -1)."""
    found: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for index, border in enumerate(drawing.borders):
        if border.closed:
            continue
        points = border.points()
        for end in (0, -1):
            found.setdefault((int(round(points[end][0] * 4)), int(round(points[end][1] * 4))), []).append((index, end))
    return found


def occlusions(drawing: Drawing) -> None:
    """Marks the borders along which one face hides another, as far as the drawing itself says.

    Where three borders meet and two of them run straight through (a T), the third ends against
    an edge that goes on: the face on the far side of that edge is nearer the camera and hides the
    two faces the third border divides. Faces that meet in a corner of the part make a fork or an
    arrow instead, with no border running through."""
    for ends in meetings(drawing).values():
        if len(ends) != 3:
            continue
        ways = [drawing.borders[index].leaving(end) for index, end in ends]
        best = None
        for a, b in ((0, 1), (0, 2), (1, 2)):
            bend = math.pi - math.acos(float(np.clip(ways[a] @ ways[b], -1, 1)))
            if best is None or bend < best[0]:
                best = (bend, a, b)
        bend, a, b = best
        stem = 3 - a - b
        off = math.acos(float(np.clip(abs(ways[stem] @ ways[a]), -1, 1)))
        if bend > T_BAR or off < T_STEM:
            continue
        bar = [drawing.borders[ends[a][0]], drawing.borders[ends[b][0]]]
        common = set(bar[0].faces) & set(bar[1].faces)
        if len(common) != 1:
            continue
        front = common.pop()
        if front == 0:
            continue   # the outline runs on and an edge ends against it: nothing is hidden there
        for border in bar:
            if border.front is None:
                border.front, border.why = front, "T"
            elif border.front != front:
                border.front, border.why = None, "two Ts disagree"


def read(seen: verify.Observed) -> Drawing:
    """The drawing of a view. Remembered on the view: several steps ask for the same one."""
    known = getattr(seen, "_drawing", None)
    if known is not None:
        return known
    edges = edge_pixels(seen)
    labels = partition(seen, edges)
    darker, _ = darkness(seen.image)
    outline = cv2.morphologyEx(seen.mask.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    weight = np.maximum(np.maximum(darker, 0), jumps(seen.image)) * edges + 40.0 * outline
    runs, edges_found = [], []
    for area, other, points, closed in _borders(labels):
        fitted = fit(_refine(points, weight), closed)
        for stroke in fitted:
            stroke.faces = (area, other)
        if fitted:
            runs.append(fitted)
            edges_found.append(Border((area, other), fitted, closed or fitted[0].kind == "circle"))
    _join(runs)
    drawing = Drawing(labels, [stroke for run in runs for stroke in run], borders=edges_found)
    occlusions(drawing)

    # the lines that divide nothing: edge pixels away from every border
    borders = np.zeros(labels.shape, np.uint8)
    for stroke in drawing.strokes:
        cv2.polylines(borders, [np.rint(stroke.points).astype(np.int32).reshape(-1, 1, 2)], False, 1, 1)
    alone = edges & (cv2.dilate(borders, np.ones((5, 5), np.uint8)) == 0)
    if alone.any():
        for chain in between.chains(between.thin(alone)):
            if len(chain) < MIN_LOOSE_PX:
                continue
            face = int(labels[int(chain[len(chain) // 2][1]), int(chain[len(chain) // 2][0])])
            for stroke in fit(_refine(chain, weight)):
                stroke.faces = (face, face)
                drawing.loose.append(stroke)
    seen._drawing = drawing
    return drawing
