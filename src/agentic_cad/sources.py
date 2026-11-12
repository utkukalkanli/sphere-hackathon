"""Sources derived from the views alone: what the pictures say before any solid of ours is asked.

The views are shaded pictures; the steps need geometry. What can be read off them without knowing
the part is gathered here, once per case, so that every step sees the same (`Evidence`):

- the drawing of every view (drawing.py): its faces as closed areas, its edges as lines and arcs;
- `areas`: every face of a view with its tone and what kind of face that makes it: in a straight
  view facing its camera, slanted or curved; in an isometric view flat or curved;
- `faces`: the flat faces of the two isometric views that are square to an axis. An isometric
  camera sees three sides of the part and the renderer gives each its own tone, so a flat face
  with the tone of the +X, -Y or +Z side (-X, +Y, -Z from the other camera) is such a face: a
  normal, from the tone table alone;
- the plane of such a face. It lies at x, y or z = c. For every c its pixels are carried along
  the isometric camera's line of sight to that plane and from there into the straight view that
  looks along the axis. At the right c they land inside one face of that view that looks at its
  camera, and the border of the isometric face lands on borders there. Where one c stands out
  the face has a `level`, and with it every pixel of the face a point in space (`depth`).

Which renderer made the views (the tone table) is read off the isometric views too: the table
whose three side tones explain more of their flat area.

Nothing here cuts. Positions are in mm from the bounding-box min corner, as everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from agentic_cad import drawing
from agentic_cad import lines as between
from agentic_cad import regions, tone, verify

AXIS_TONE = 4          # a flat face is an axis face when its tone is this close to that side's tone
OTHER_TONE = 6         # ... and further than this from the tones of the other two sides
MIN_FACE_PX = 150      # smaller faces are not judged
EDGE_PX = 2            # pixels this close to a border are not read: the line darkens them
MIN_INSIDE = 0.97      # share of a face's pixels that must land on the part in the straight view
MIN_ONE_AREA = 0.85    # ... and inside one face of it
MIN_ON_LINES = 0.3     # share of the border of a face that lands on borders at its level
MIN_STANDS_OUT = 0.15  # ... and how far that is above the share at a usual level
LINE_REACH_PX = 2.5    # a border and a border this close are the same (as in verify)
SURE_PX = 10000        # faces at least this large had the right level every time it stood out
FAR_SHARE = float(__import__("os").environ.get("HULL_FAR_SHARE", 0.9))   # a level this close to the best share counts as explaining as much
PEAK_PX = 3            # a level is a peak if no level this close has a larger share
SAME_LEVEL_PX = 3      # two faces whose levels are this close lie in one plane
SAMPLES = 1500         # pixels of a face, and of its border, that are carried over

# the three sides an isometric camera sees: axis, its sign, the straight view that looks at that side
SIDES = {"iso_front_right_top": ((0, 1, "right"), (1, -1, "front"), (2, 1, "top")),
         "iso_back_left_bottom": ((0, -1, "left"), (1, 1, "back"), (2, -1, "bottom"))}


@dataclass
class Face:
    view: str              # the isometric view
    label: int             # its value in the label image of that view
    area: int              # pixels
    axis: int              # 0, 1, 2: the face is square to x, y or z
    sign: int              # +1 or -1: the way it looks along that axis
    straight: str          # the straight view that looks at it
    level: float | None = None   # mm along the axis, from the box's min corner
    on_lines: float = 0.0  # share of its border that lands on borders of the straight view there
    usual: float = 0.0     # the same share at a usual level
    sure: bool = False     # the level stands out and the face is large enough to trust it

    def summary(self) -> dict:
        return {"view": self.view, "axis": "xyz"[self.axis], "sign": self.sign, "area_px": self.area,
                "level_mm": None if self.level is None else round(self.level, 3),
                "on_lines": round(self.on_lines, 2), "usual": round(self.usual, 2), "sure": self.sure}


def steps(observed: verify.Observed) -> np.ndarray:
    """Where two faces meet without a line that stands out: neighbouring pixels a jump in tone apart."""
    tones = regions.tone_of(observed.image)
    jump = np.zeros(tones.shape, bool)
    jump[:, :-1] |= np.abs(np.diff(tones, axis=1)) >= regions.TONE_JUMP
    jump[:-1, :] |= np.abs(np.diff(tones, axis=0)) >= regions.TONE_JUMP
    return jump & observed.mask & ~observed.lines


def borders(labels: np.ndarray) -> np.ndarray:
    """The pixels of a label image that have another label beside them."""
    result = np.zeros(labels.shape, bool)
    across, down = labels[:, :-1] != labels[:, 1:], labels[:-1, :] != labels[1:, :]
    result[:, :-1] |= across
    result[:, 1:] |= across
    result[:-1, :] |= down
    result[1:, :] |= down
    return result


def _measured(seen: verify.Observed, labels: np.ndarray) -> list[tuple]:
    """Per face large enough, largest first: label, area, box, median tone and spread (8th to 92nd
    percentile), read away from the borders and with the light's falloff taken out (regions.py)."""
    tones = regions.tone_of(seen.image)
    reach = np.ones((2 * EDGE_PX + 1, 2 * EDGE_PX + 1), np.uint8)
    readable = cv2.dilate(borders(labels).astype(np.uint8), reach) == 0
    count = int(labels.max()) + 1
    rows, cols = np.nonzero(labels)
    values = labels[rows, cols]
    area = np.bincount(values, minlength=count)
    top, left = np.full(count, labels.shape[0]), np.full(count, labels.shape[1])
    bottom, right = np.zeros(count, int), np.zeros(count, int)
    np.minimum.at(top, values, rows)
    np.minimum.at(left, values, cols)
    np.maximum.at(bottom, values, rows)
    np.maximum.at(right, values, cols)
    size = seen.mask.shape[0]
    result = []
    for label in np.argsort(-area):
        if label == 0 or area[label] < MIN_FACE_PX:
            continue
        x, y, w, h = int(left[label]), int(top[label]), int(right[label] - left[label] + 1), int(bottom[label] - top[label] + 1)
        window = (slice(y, y + h), slice(x, x + w))
        inside = labels[window] == label
        core = inside & readable[window]
        chosen = core if core.sum() >= MIN_FACE_PX // 4 else inside
        shades = tones[window][chosen]
        low, middle, high = np.percentile(shades, (8, 50, 92))
        spread = high - low
        allowed = regions.MAX_FALLOFF * min(1.0, max(w, h) / (size / 2))
        if regions.FLAT_SPREAD < spread <= regions.FLAT_SPREAD + allowed:
            inner_rows, inner_cols = np.nonzero(chosen)
            spread = regions._without_falloff(shades, inner_cols + x, inner_rows + y, size, spread, allowed)
        result.append((int(label), int(area[label]), (x, y, w, h), int(middle), int(round(spread))))
    return result


FACING_NEAR = 8        # the facing tone of a case lies this close to the table's tone for a face square to the camera


def _facing_tone(measured: dict[str, list[tuple]]) -> int | None:
    """The tone of a face square to the camera in the straight views of this case. The commonest
    flat tone is not it when slanted faces fill the views (practice 9: its two banks, bright and
    dark, are most of the front view). The renderer's table says what a facing face looks like,
    and the table is known from the isometric views; the case's own tone is the commonest flat
    tone near that."""
    straight = {name: items for name, items in measured.items() if name in verify.ORTHO_VIEWS}
    flat = {name: [regions.Region(name, label, area, box, "flat" if spread <= regions.FLAT_SPREAD else "curved", tone_, spread)
                   for label, area, box, tone_, spread in items] for name, items in measured.items() if name in SIDES}
    table = choose_table({name: (None, items) for name, items in flat.items()})
    if table is None:
        return regions.facing_tone(straight)
    expected = float(table.tone(np.array([0.0, 0.0, 1.0])))
    counts = np.zeros(256)
    for items in straight.values():
        for _, area, _, middle, spread in items:
            if spread <= regions.FLAT_SPREAD and abs(middle - expected) <= FACING_NEAR:
                counts[middle] += area
    if not counts.any():
        return int(round(expected))
    return int(np.convolve(counts, np.ones(2 * regions.FACING_TOLERANCE + 1), mode="same").argmax())


def areas(observed: dict[str, verify.Observed]) -> dict[str, tuple[np.ndarray, list[regions.Region]]]:
    """Per view the label image of its drawing and its faces, largest first, each with its kind:
    "facing", "slanted" or "curved" in a straight view, "flat" or "curved" in an isometric one."""
    labels = {name: drawing.read(seen).labels for name, seen in observed.items()}
    measured = {name: _measured(observed[name], labels[name]) for name in observed}
    facing = _facing_tone(measured)
    result = {}
    for name, items in measured.items():
        listed = []
        for label, area, box, middle, spread in items:
            if name in verify.ORTHO_VIEWS:
                kind = regions.kind_of(middle, spread, facing)
            else:
                kind = "flat" if spread <= regions.FLAT_SPREAD else "curved"
            listed.append(regions.Region(name, label, area, box, kind, middle, spread))
        result[name] = (labels[name], listed)
    return result


def side_tones(table: tone.Table, view: str) -> list[float]:
    """The tones of the three sides an isometric camera sees."""
    result = []
    for axis, sign, _ in SIDES[view]:
        normal = np.zeros(3)
        normal[axis] = sign
        result.append(float(table.tone(tone.in_camera(normal, view))))
    return result


def _side(item: regions.Region, tones: list[float]) -> int | None:
    if item.kind != "flat":
        return None
    off = [abs(item.tone - t) for t in tones]
    side = int(np.argmin(off))
    return side if off[side] <= AXIS_TONE and sorted(off)[1] > OTHER_TONE else None


def choose_table(found: dict) -> tone.Table | None:
    """The tone table of the renderer of these views, from the views alone: the one whose side
    tones explain more of the flat area of the isometric views. The organisers' unless another
    explains more."""
    tables = tone.tables()
    best, most = tables.get(tone.DEFAULT), -1
    for name in sorted(tables, key=lambda n: n != tone.DEFAULT):
        explained = sum(item.area for view in SIDES if view in found for item in found[view][1]
                        if _side(item, side_tones(tables[name], view)) is not None)
        if explained > most:
            best, most = tables[name], explained
    return best


def _sample(mask: np.ndarray) -> np.ndarray:
    rows, cols = np.nonzero(mask)
    if len(rows) > SAMPLES:
        keep = np.random.default_rng(0).choice(len(rows), SAMPLES, replace=False)
        rows, cols = rows[keep], cols[keep]
    return np.stack([cols, rows], axis=1).astype(float)


def _carried(face: Face, points: np.ndarray, level: float, observed: dict, dims: dict) -> np.ndarray:
    """Points of the isometric view (mm from the box centre, at depth 0) carried along the line
    of sight onto the plane at `level` and into the face's straight view: pixels (column, row)."""
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    sight = verify.camera(face.view)[2]
    straight = observed[face.straight]
    along = (level - centre[face.axis] - points[:, face.axis]) / sight[face.axis]
    pixels, _ = between.land(face.straight, points + along[:, None] * sight, straight.mm_per_px, straight.mask.shape[0])
    return np.rint(pixels).astype(int)


def _scan(face: Face, area: np.ndarray, rim: np.ndarray, observed: dict, dims: dict, found: dict, near_border: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For every level an axis face of an isometric view could lie at: the share of its border
    that lands on borders of the straight view, or -1 where the face does not land inside one
    facing face there."""
    iso, straight = observed[face.view], observed[face.straight]
    size = iso.mask.shape[0]
    labels, items = found[face.straight]
    facing = np.zeros(int(labels.max()) + 1, bool)
    for item in items:
        facing[item.label] = item.kind == "facing"
    zero = np.zeros(SAMPLES)
    inner, border = _sample(area), _sample(rim)
    inner = between.lift(face.view, inner, zero[:len(inner)], dims, iso.mm_per_px, size)
    border = between.lift(face.view, border, zero[:len(border)], dims, iso.mm_per_px, size)
    levels = np.arange(0.0, dims["xyz"[face.axis]] + straight.mm_per_px, straight.mm_per_px)
    share = np.full(len(levels), -1.0)
    for index, level in enumerate(levels):
        pixels = _carried(face, inner, level, observed, dims)
        ok = ((pixels >= 0) & (pixels < size)).all(axis=1)
        if ok.mean() < 0.98:
            continue
        hit = labels[pixels[ok, 1], pixels[ok, 0]]
        counts = np.bincount(hit, minlength=len(facing))
        counts[0] = 0
        main = int(counts.argmax())
        if straight.mask[pixels[ok, 1], pixels[ok, 0]].mean() < MIN_INSIDE or counts[main] < MIN_ONE_AREA * len(hit) \
                or not facing[main]:
            continue
        pixels = _carried(face, border, level, observed, dims)
        ok = ((pixels >= 0) & (pixels < size)).all(axis=1)
        share[index] = float(near_border[pixels[ok, 1], pixels[ok, 0]].sum() / len(pixels)) if len(pixels) else 0.0
    return levels, share


def _pick(face: Face, levels: np.ndarray, share: np.ndarray, taken: list[float], pixel: float) -> None:
    """The level of a face: where most of its border lands on borders. Levels of neighbouring faces
    that look the same way are left out: two parallel faces with a border between them are a
    step, and at the neighbour's level it is the neighbour's edge that lands on the lines (a floor
    seen past the rim of its pocket lands there on the rim)."""
    possible = share >= 0
    for level in taken:
        possible &= np.abs(levels - level) > SAME_LEVEL_PX * pixel
    face.level, face.on_lines, face.usual, face.sure = None, 0.0, 0.0, False
    if not possible.any():
        return
    allowed = np.where(possible, share, -1.0)
    best = int(allowed.argmax())
    # An edge of the face's outline is its own or belongs to something nearer the camera that
    # hides part of it; at the level of that nearer thing its edge lands on lines just as well.
    # So of the levels that explain nearly as much, the one farthest from the camera is the face's.
    if FAR_SHARE < 1 and allowed[best] > 0:
        padded = np.pad(allowed, PEAK_PX, constant_values=-1.0)
        windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * PEAK_PX + 1)
        peaks = np.nonzero((allowed >= FAR_SHARE * allowed[best]) & (allowed >= windows.max(axis=1)))[0]
        if len(peaks):
            best = int(peaks.min() if face.sign > 0 else peaks.max())
    face.level, face.on_lines, face.usual = float(levels[best]), float(share[best]), float(np.median(share[possible]))
    face.sure = face.area >= SURE_PX and face.on_lines >= MIN_ON_LINES and face.on_lines - face.usual >= MIN_STANDS_OUT


def faces(observed: dict[str, verify.Observed], dims: dict[str, float], table: tone.Table | None = None,
          found: dict | None = None) -> dict[str, tuple[np.ndarray, list[Face]]]:
    """Per isometric view: its label image and the faces square to an axis, largest first, each
    with its level where one stands out. `found` is `areas(observed)`; `table` the renderer's tone
    table, read off the views if not given."""
    found = areas(observed) if found is None else found
    table = choose_table(found) if table is None else table
    result = {}
    if table is None:
        return result
    reach = np.ones((2 * int(LINE_REACH_PX) + 1,) * 2, np.uint8)
    near = {name: cv2.dilate(borders(found[name][0]).astype(np.uint8), reach) > 0
            for name in verify.ORTHO_VIEWS if name in found}
    for view, sides in SIDES.items():
        if view not in found:
            continue
        labels, items = found[view]
        tones = side_tones(table, view)
        rims = borders(labels)
        listed, scans = [], {}
        for item in items:
            side = _side(item, tones)
            if side is None:
                continue
            axis, sign, straight = sides[side]
            face = Face(view, item.label, item.area, axis, sign, straight)
            if straight in near:
                area = labels == item.label
                scans[face.label] = _scan(face, area, area & rims, observed, dims, found, near[straight])
            listed.append(face)
        # which faces lie side by side
        beside: dict[int, set[int]] = {}
        for first, second in ((labels[:, :-1], labels[:, 1:]), (labels[:-1, :], labels[1:, :])):
            differ = (first != second) & (first > 0) & (second > 0)
            for a, b in set(zip(first[differ].tolist(), second[differ].tolist())):
                beside.setdefault(a, set()).add(b)
                beside.setdefault(b, set()).add(a)
        by_label = {face.label: face for face in listed}
        for _ in range(3):   # a level that becomes sure can take a neighbour's level away, a few times over
            before = [(face.level, face.sure) for face in listed]
            for face in listed:
                if face.label not in scans:
                    continue
                taken = [other.level for other in (by_label.get(label) for label in beside.get(face.label, ()))
                         if other is not None and other is not face and other.axis == face.axis and other.sure]
                _pick(face, *scans[face.label], taken, observed[face.straight].mm_per_px)
            if before == [(face.level, face.sure) for face in listed]:
                break
        result[view] = (labels, listed)
    return result


def footprint(face: Face, labels: np.ndarray, observed: dict[str, verify.Observed], dims: dict[str, float]) -> np.ndarray:
    """Where the pixels of a face with a level land in its straight view: (column, row), those inside the picture."""
    iso = observed[face.view]
    size = iso.mask.shape[0]
    points = _sample(labels == face.label)
    points = between.lift(face.view, points, np.zeros(len(points)), dims, iso.mm_per_px, size)
    pixels = _carried(face, points, face.level, observed, dims)
    return pixels[((pixels >= 0) & (pixels < size)).all(axis=1)]


def normals(observed: dict[str, verify.Observed], found: dict) -> dict[str, np.ndarray]:
    """Per isometric view the normal per pixel (size, size, 3) in the frame of the box; zero where
    the picture does not say."""
    result = {}
    for view, (labels, listed) in found.items():
        table = np.zeros((int(labels.max()) + 1, 3))
        for face in listed:
            table[face.label, face.axis] = face.sign
        result[view] = table[labels]
    return result


def depth(observed: dict[str, verify.Observed], dims: dict[str, float], found: dict, sure: bool = True) -> dict[str, np.ndarray]:
    """Per isometric view the depth per pixel in mm towards the camera from the centre of the box
    (as verify.Drawing.depth), nan where no face with a level is seen."""
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    result = {}
    for view, (labels, listed) in found.items():
        seen = observed[view]
        size = seen.mask.shape[0]
        sight = verify.camera(view)[2]
        image = np.full(labels.shape, np.nan)
        for face in listed:
            if face.level is None or (sure and not face.sure):
                continue
            rows, cols = np.nonzero(labels == face.label)
            points = between.lift(view, np.stack([cols, rows], axis=1).astype(float), np.zeros(len(rows)), dims,
                                  seen.mm_per_px, size)
            image[rows, cols] = (face.level - centre[face.axis] - points[:, face.axis]) / sight[face.axis]
        result[view] = image
    return result


def planes_seen_straight(observed: dict[str, verify.Observed], dims: dict[str, float], iso: dict, found: dict) -> dict:
    """Per straight view: for the faces an isometric face with a level lands in, (label, depth in mm
    towards that view's camera from the centre of the box, sure)."""
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    result: dict[str, list[tuple[int, float, bool]]] = {}
    for view, (labels, listed) in iso.items():
        for face in listed:
            if face.level is None or face.straight not in found:
                continue
            pixels = footprint(face, labels, observed, dims)
            if not len(pixels):
                continue
            hit = np.bincount(found[face.straight][0][pixels[:, 1], pixels[:, 0]])
            hit[0] = 0
            if hit.max() >= MIN_ONE_AREA * len(pixels):
                result.setdefault(face.straight, []).append(
                    (int(hit.argmax()), face.sign * (face.level - centre[face.axis]), face.sure))
    return result


# -- pictures, for the dashboard ----------------------------------------------------------------------

KINDS = ("lines", "faces", "normals", "depth")   # what `pictures` draws
DRAW_SCALE = 2         # the pictures are drawn at this many times the size of the views
STROKE_PX = 1.2        # width of a drawn edge, in pixels of the view
PAPER = (244, 244, 244)                          # BGR of the part where a picture says nothing
UNKNOWN = (150, 150, 150)                        # ... and where a source could say something and does not


def _palette(count: int) -> np.ndarray:
    """A colour per face, neighbours in the list far apart in hue."""
    hue = (np.arange(count) * 0.61803398875 % 1.0 * 180).astype(np.uint8)
    hsv = np.stack([hue, np.full(count, 95, np.uint8), np.full(count, 238, np.uint8)], axis=1)[None]
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0]


def _sheet(seen: verify.Observed, colour: np.ndarray, sketch: drawing.Drawing) -> bytes:
    """A picture of a view: `colour` (BGR per pixel of the view) under the edges of its drawing in
    black, at DRAW_SCALE times the view's size, transparent outside the part."""
    size = seen.mask.shape[0] * DRAW_SCALE
    image = cv2.resize(colour, (size, size), interpolation=cv2.INTER_NEAREST)
    alpha = cv2.resize(seen.mask.astype(np.uint8) * 255, (size, size), interpolation=cv2.INTER_NEAREST)
    ink = np.zeros((size, size), np.uint8)
    shift = 4
    curves = [np.rint((stroke.points + 0.5) * DRAW_SCALE * 2 ** shift).astype(np.int32).reshape(-1, 1, 2)
              for stroke in (*sketch.strokes, *sketch.loose)]
    cv2.polylines(ink, curves, False, 255, max(1, round(STROKE_PX * DRAW_SCALE)), cv2.LINE_AA, shift)
    weight = ink[:, :, None] / 255.0
    image = (image * (1 - weight)).astype(np.uint8)
    return cv2.imencode(".png", np.dstack([image, np.maximum(alpha, ink)]))[1].tobytes()


def sketches(seen: verify.Observed) -> dict[str, bytes]:
    """The two pictures that need nothing but the view's own drawing, as PNGs: "lines", the edges
    alone, black on the part, and "faces", every face in a colour of its own."""
    sketch = drawing.read(seen)
    labels = sketch.labels
    blank = np.zeros(seen.image.shape, np.uint8)
    blank[seen.mask] = PAPER
    coloured = _palette(int(labels.max()) + 1)[labels]
    coloured[labels == 0] = 0
    return {"lines": _sheet(seen, blank, sketch), "faces": _sheet(seen, coloured, sketch)}


def pictures(observed: dict[str, verify.Observed], dims: dict[str, float], table: tone.Table | None = None) -> dict[tuple[str, str], bytes]:
    """Every source of every view as a PNG, keyed by (view, kind), over the view's drawing:
    - lines: the edges alone, black on the part;
    - faces: every face in a colour of its own;
    - normals: the normal as a colour ((n + 1) / 2 as red, green, blue): the faces of a straight
      view that look at its camera, the faces of an isometric view that are square to an axis;
    - depth: mm towards the camera, near warm: the faces whose plane was found, pale where it is
      not sure. In a straight view these are the faces the isometric faces land in.
    Grey: the picture does not say."""
    result = {}
    found = areas(observed)
    iso = faces(observed, dims, table, found)
    landed = planes_seen_straight(observed, dims, iso, found)
    reach = float(np.linalg.norm([dims["x"], dims["y"], dims["z"]])) / 2

    def heat(value: np.ndarray) -> np.ndarray:
        shade = np.clip((np.nan_to_num(value) + reach) / (2 * reach), 0, 1)
        return cv2.applyColorMap((shade * 255).astype(np.uint8), cv2.COLORMAP_TURBO)

    def pale(colour: np.ndarray) -> np.ndarray:
        return (0.4 * colour + 0.6 * np.array(UNKNOWN)).astype(np.uint8)

    every, certain = depth(observed, dims, iso, sure=False), depth(observed, dims, iso)
    for view, seen in observed.items():
        sketch = drawing.read(seen)
        labels = sketch.labels
        for kind, png in sketches(seen).items():
            result[view, kind] = png

        direction = np.zeros(seen.image.shape, np.uint8)
        direction[seen.mask] = UNKNOWN
        distance = direction.copy()
        if view in verify.ORTHO_VIEWS:
            towards = verify.camera(view)[2]
            colour = ((towards[::-1] + 1) / 2 * 235).astype(np.uint8)
            for item in found[view][1]:
                if item.kind == "facing":
                    direction[labels == item.label] = colour
            for label, value, sure in landed.get(view, []):
                tint = heat(np.full((1, 1), value))[0, 0]
                distance[labels == label] = tint if sure else pale(tint)
        elif view in iso:
            for face in iso[view][1]:
                normal = np.zeros(3)
                normal[face.axis] = face.sign
                direction[labels == face.label] = ((normal[::-1] + 1) / 2 * 235).astype(np.uint8)
            known = np.isfinite(every[view])
            tint = heat(every[view])
            distance[known] = tint[known]
            unsure = known & ~np.isfinite(certain[view])
            distance[unsure] = pale(tint[unsure])
        result[view, "normals"] = _sheet(seen, direction, sketch)
        result[view, "depth"] = _sheet(seen, distance, sketch)
    return result
