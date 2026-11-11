"""Rebuild a silhouette outline from straight lines and circular arcs instead of pixels.

An outline traced from a mask follows the pixel grid: a slanted edge comes out as hundreds of small
horizontal and vertical steps, a circle as a many-sided polygon, and both end up as staircase faces
on the solid. Here the traced outline is split at its corners and each piece is replaced by the line
or the circular arc that fits it, so the solid gets flat and cylindrical faces.

A loop is a list of segments, each one of
    ("line", start, end)
    ("arc", start, middle, end)      three points on the circle
    ("circle", centre, radius)       a full circle, the only segment of its loop
with points as (a, b) in mm. A piece that is neither straight nor circular stays a short polyline.

Known radii and circle centres (from facts.json) can be passed in: a fitted circle that lands close
to one is replaced by the exact one.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

CORNER_PX = 1.2        # how far a line may be from the traced pixels
ARC_PX = 1.0           # how far an arc may be from the traced pixels
MIN_SWEEP = math.radians(12)   # a flatter arc is left as lines
MIN_SAGITTA_PX = 1.0   # an arc must bulge at least this much from its chord
AXIS_DRIFT_PX = 1.0    # a line that drifts less than this (or 0.4 %) is exactly horizontal / vertical
AXIS_DRIFT = 0.004
SNAP_RADIUS = 0.02     # a fitted radius within 2 % (or 1 px) of a known one becomes that one
SNAP_CENTRE_PX = 1.5   # a fitted centre this close to a known one becomes that one

Point = tuple[float, float]


def fit_circle(points: np.ndarray) -> tuple[np.ndarray, float]:
    """Least-squares circle: algebraic start, then a few geometric (Gauss-Newton) steps."""
    x, y = points[:, 0], points[:, 1]
    system = np.column_stack([2 * x, 2 * y, np.ones(len(points))])
    cx, cy, c = np.linalg.lstsq(system, x * x + y * y, rcond=None)[0]
    centre, radius = np.array([cx, cy]), math.sqrt(max(c + cx * cx + cy * cy, 1e-12))
    for _ in range(6):
        offset = points - centre
        distance = np.maximum(np.hypot(offset[:, 0], offset[:, 1]), 1e-12)
        jacobian = np.column_stack([-offset[:, 0] / distance, -offset[:, 1] / distance, -np.ones(len(points))])
        step = np.linalg.lstsq(jacobian, -(distance - radius), rcond=None)[0]
        if not np.all(np.isfinite(step)):
            break
        centre, radius = centre + step[:2], radius + step[2]
    return centre, abs(float(radius))


def fit_centre(points: np.ndarray, radius: float, start: np.ndarray) -> np.ndarray:
    """Best centre for a circle of a given radius."""
    centre = start.copy()
    for _ in range(8):
        offset = points - centre
        distance = np.maximum(np.hypot(offset[:, 0], offset[:, 1]), 1e-12)
        jacobian = np.column_stack([-offset[:, 0] / distance, -offset[:, 1] / distance])
        step = np.linalg.lstsq(jacobian, -(distance - radius), rcond=None)[0]
        if not np.all(np.isfinite(step)):
            break
        centre = centre + step
    return centre


def _residual(points: np.ndarray, centre: np.ndarray, radius: float) -> float:
    return float(np.abs(np.hypot(points[:, 0] - centre[0], points[:, 1] - centre[1]) - radius).max())


def _snap(points: np.ndarray, centre: np.ndarray, radius: float, px: float, known: dict | None,
          limit: float) -> tuple[np.ndarray, float]:
    """Replace a fitted circle by a known centre and/or radius when it still fits the pixels."""
    if not known:
        return centre, radius
    for known_centre, known_radius in known.get("circles", []):
        if (math.hypot(centre[0] - known_centre[0], centre[1] - known_centre[1]) <= SNAP_CENTRE_PX * px
                and abs(radius - known_radius) <= max(SNAP_RADIUS * radius, 1.0 * px)
                and _residual(points, np.array(known_centre), known_radius) <= limit * 1.5):
            return np.array(known_centre, dtype=float), float(known_radius)
    best = min(known.get("radii", []), key=lambda r: abs(r - radius), default=None)
    if best is not None and abs(best - radius) <= max(SNAP_RADIUS * radius, 1.0 * px):
        moved = fit_centre(points, best, centre)
        if _residual(points, moved, best) <= limit * 1.25:
            return moved, float(best)
    return centre, radius


def fit_loop(contour: np.ndarray, to_mm, px: float, known: dict | None = None, arcs: bool = True) -> list[tuple]:
    """contour: the dense closed pixel outline from cv2.findContours (CHAIN_APPROX_NONE).
    to_mm maps Nx2 pixel coordinates to mm, px is the size of a pixel in mm."""
    dense = to_mm(contour.reshape(-1, 2).astype(float))
    count = len(dense)

    # A hole or boss seen end-on: the whole outline is one circle
    if arcs and count >= 12:
        centre, radius = fit_circle(dense)
        if radius > 2 * px and _residual(dense, centre, radius) <= ARC_PX * px:
            centre, radius = _snap(dense, centre, radius, px, known, ARC_PX * px)
            return [("circle", (float(centre[0]), float(centre[1])), float(radius))]

    # Corners: the vertices of a coarse polygon, as positions in the dense outline
    polygon = cv2.approxPolyDP(contour, CORNER_PX, True).reshape(-1, 2)
    if len(polygon) < 3:
        return []
    pixels = contour.reshape(-1, 2)
    breaks, cursor = [], 0
    for vertex in polygon:
        matches = np.where((pixels[cursor:] == vertex).all(axis=1))[0]
        index = cursor + int(matches[0]) if len(matches) else int(np.where((pixels == vertex).all(axis=1))[0][0])
        breaks.append(index)
        cursor = index
    breaks = sorted(set(breaks))
    if len(breaks) < 3:
        return []

    def run(start: int, end: int) -> np.ndarray:
        """Dense points from break `start` to break `end` (indices may wrap past the last break)."""
        first, last = breaks[start % len(breaks)], breaks[end % len(breaks)]
        return dense[first:last + 1] if last > first else np.vstack([dense[first:], dense[:last + 1]])

    total = len(breaks)
    # Start at the sharpest corner, so that no arc is split by where the outline happens to begin
    def turn(i: int) -> float:
        before = dense[breaks[i]] - dense[breaks[i - 1]]
        after = dense[breaks[(i + 1) % total]] - dense[breaks[i]]
        return abs(math.atan2(before[0] * after[1] - before[1] * after[0], float(before @ after)))
    origin = max(range(total), key=turn)

    pieces = []  # (kind, first break, last break, circle or None), break numbers counted from `origin`
    i = 0
    while i < total:
        best = None
        if arcs:
            j = i + 2
            while j <= min(i + total - 1, total):
                points = run(origin + i, origin + j)
                centre, radius = fit_circle(points)
                if radius > 50 * count * px or _residual(points, centre, radius) > ARC_PX * px:
                    break
                best = (j, centre, radius, points)
                j += 1
        if best is not None:
            j, centre, radius, points = best
            angles = np.unwrap(np.arctan2(points[:, 1] - centre[1], points[:, 0] - centre[0]))
            sweep = abs(float(angles[-1] - angles[0]))
            if sweep >= MIN_SWEEP and radius * (1 - math.cos(min(sweep, math.pi) / 2)) >= MIN_SAGITTA_PX * px:
                centre, radius = _snap(points, centre, radius, px, known, ARC_PX * px)
                pieces.append(("arc", i, j, (centre, radius, points)))
                i = j
                continue
        pieces.append(("line", i, i + 1, None))
        i += 1

    # Vertices: one per break that starts a piece. Lines that hardly drift become exactly axis-parallel.
    vertex = {piece[1] % total: dense[breaks[(origin + piece[1]) % total]].astype(float).copy() for piece in pieces}
    fixed = {key: [False, False] for key in vertex}
    for kind, first, last, _ in pieces:
        if kind != "line":
            continue
        points = run(origin + first, origin + last)
        span = np.abs(points[-1] - points[0])
        length = float(np.hypot(*span))
        for axis in (0, 1):  # axis 0: constant a (vertical line), axis 1: constant b (horizontal line)
            if span[axis] <= max(AXIS_DRIFT_PX * px, AXIS_DRIFT * length) and span[1 - axis] > 3 * px:
                value = float(np.median(points[:, axis]))
                for key in (first % total, last % total):
                    vertex[key][axis] = value
                    fixed[key][axis] = True

    # An arc's ends go onto its circle, so the face is exactly cylindrical.
    # - Next to an axis-parallel line the circle nearly touches (a fillet, the end of a slot), the
    #   circle is moved so that it touches the line exactly, and the end is the touching point.
    # - Next to an axis-parallel line it crosses, the end is where the two meet.
    # - Otherwise the end is the nearest point of the circle.
    for number, (kind, first, last, circle) in enumerate(pieces):
        if kind != "arc":
            continue
        centre, radius, points = circle
        centre = centre.copy()
        ends = (first % total, last % total)
        touching = {0: [], 1: []}  # axis -> [(end, coordinate of the line, side of the centre it is on)]
        for key in ends:
            for axis in (0, 1):
                if fixed[key][axis] and not fixed[key][1 - axis]:
                    offset = vertex[key][axis] - centre[axis]
                    if abs(abs(offset) - radius) <= 2 * px:
                        touching[axis].append((key, float(vertex[key][axis]), math.copysign(1.0, offset)))
        moved_centre, moved_radius = centre.copy(), radius
        for axis, found in touching.items():
            if len(found) == 1:
                moved_centre[axis] = found[0][1] - found[0][2] * moved_radius
            elif len(found) == 2 and found[0][2] != found[1][2]:  # between two parallel lines
                half = abs(found[0][1] - found[1][1]) / 2
                if abs(half - radius) <= 1.5 * px:
                    moved_centre[axis], moved_radius = (found[0][1] + found[1][1]) / 2, half
                else:
                    touching[axis] = []
            else:
                touching[axis] = []
        tangent_ends = {key for found in touching.values() for key, _, _ in found}
        if tangent_ends and _residual(points, moved_centre, moved_radius) <= 2 * ARC_PX * px:
            centre, radius = moved_centre, moved_radius
            for axis, found in touching.items():
                for key, value, _ in found:
                    vertex[key][axis] = value
                    vertex[key][1 - axis] = centre[1 - axis]
        else:
            tangent_ends = set()
        for key in ends:
            if key in tangent_ends:
                continue
            point = vertex[key]
            placed = None
            for axis in (0, 1):
                if fixed[key][axis] and not fixed[key][1 - axis]:
                    offset = point[axis] - centre[axis]
                    if abs(offset) <= radius:
                        reach = math.sqrt(radius * radius - offset * offset)
                        candidate = point.copy()
                        candidate[1 - axis] = centre[1 - axis] + math.copysign(reach, point[1 - axis] - centre[1 - axis])
                        if np.hypot(*(candidate - point)) <= 2.5 * px:
                            placed = candidate
            if placed is None and not (fixed[key][0] or fixed[key][1]):
                direction = point - centre
                norm = float(np.hypot(*direction))
                if norm > 1e-9:
                    placed = centre + direction / norm * radius
                    if np.hypot(*(placed - point)) > 2.5 * px:
                        placed = None
            if placed is not None:
                vertex[key] = placed
        pieces[number] = (kind, first, last, (centre, radius, points))

    loop = []
    for kind, first, last, circle in pieces:
        start, end = vertex[first % total], vertex[last % total]
        if float(np.hypot(*(end - start))) < 1e-6 and kind == "line":
            continue
        if kind == "arc":
            centre, radius, points = circle
            middle = points[len(points) // 2] - centre
            middle = centre + middle / max(float(np.hypot(*middle)), 1e-12) * radius
            loop.append(("arc", tuple(map(float, start)), tuple(map(float, middle)), tuple(map(float, end))))
        else:
            loop.append(("line", tuple(map(float, start)), tuple(map(float, end))))
    return loop if len(loop) >= 2 else []


def sample(loop: list[tuple], step: float = math.radians(4)) -> np.ndarray:
    """Points along a loop (arcs and circles sampled every `step` radians): for areas and drawings."""
    points: list[Point] = []
    for segment in loop:
        if segment[0] == "line":
            points.append(segment[1])
        elif segment[0] == "circle":
            (cx, cy), radius = segment[1], segment[2]
            for angle in np.arange(0, 2 * math.pi, step):
                points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        else:
            start, middle, end = (np.array(p) for p in segment[1:])
            centre = circle_through(start, middle, end)
            if centre is None:
                points.append(tuple(start))
                continue
            radius = float(np.hypot(*(start - centre)))
            a0, a1, a2 = (math.atan2(p[1] - centre[1], p[0] - centre[0]) for p in (start, middle, end))
            sweep = (a2 - a0) % (2 * math.pi)
            if (a1 - a0) % (2 * math.pi) > sweep:  # the middle is on the other way round
                sweep -= 2 * math.pi
            for t in np.linspace(0, 1, max(2, int(abs(sweep) / step) + 1), endpoint=False):
                angle = a0 + sweep * t
                points.append((centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)))
    return np.array(points, dtype=float).reshape(-1, 2)


def circle_through(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray | None:
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    ux = ((a @ a) * (b[1] - c[1]) + (b @ b) * (c[1] - a[1]) + (c @ c) * (a[1] - b[1])) / d
    uy = ((a @ a) * (c[0] - b[0]) + (b @ b) * (a[0] - c[0]) + (c @ c) * (b[0] - a[0])) / d
    return np.array([ux, uy])


def signed_area(loop: list[tuple]) -> float:
    points = sample(loop)
    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def reverse(loop: list[tuple]) -> list[tuple]:
    """The same loop walked the other way (a circle has no direction of its own)."""
    return [segment if segment[0] == "circle" else (segment[0], *segment[:0:-1]) for segment in reversed(loop)]


def counts(loop: list[tuple]) -> dict[str, int]:
    result = {"line": 0, "arc": 0, "circle": 0}
    for segment in loop:
        result[segment[0]] += 1
    return result
