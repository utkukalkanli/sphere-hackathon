"""Deterministic starting solids built from the inputs alone, with no model call.

The orthographic views are rendered in the part's own frame, so each one is the part's outline in a known
plane, and the facts sheet gives the envelope in mm. Extruding the front, right and top outlines through the
envelope and intersecting the three prisms gives the part's silhouette hull: the largest solid that matches
all three outlines. It contains the real part, has the exact bounding box, and already carries every
through-opening that is visible along a principal axis. What it cannot show is material removed where no
outline reveals it (pockets, blind bores, cavities), which is what the facts and the model are for.

Everything here returns CadQuery *scripts* (text) that use the cadlib helpers, so a hull is built, measured
and traced by the same runner as any other attempt.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# view name -> (plane of its outline, axis shown to the right, axis shown up, axis it is extruded along)
HULL_VIEWS = {
    "front": ("xz", "x", "z", "y"),   # looking along +Y: X right, Z up
    "right": ("yz", "y", "z", "x"),   # looking along -X: Y right, Z up
    "top": ("xy", "x", "y", "z"),     # looking along -Z: X right, Y up
}
MIN_AREA_PX = 12.0      # contours smaller than this are render noise
MAX_POINTS = 400        # per contour; a denser outline is simplified harder


def view_mask(image_path: Path) -> np.ndarray:
    """Part pixels of a reference view at full resolution (same threshold as render.reference_mask)."""
    from PIL import Image

    return np.asarray(Image.open(image_path).convert("RGB"), dtype=np.int32).sum(axis=2) < 735


def mask_polygons(mask: np.ndarray, width_mm: float, height_mm: float, tol_px: float = 0.75):
    """Outline of a silhouette mask as polygons in mm: [(outer, [holes...]), ...].

    The mask's pixel box is the part's extent in this view, so it maps onto (0..width_mm, 0..height_mm) with
    the origin at the bottom-left corner. Curves become polylines within `tol_px` of the pixel outline."""
    import cv2

    ys, xs = np.where(mask)
    if not len(xs):
        return []
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    if x1 == x0 or y1 == y0:
        return []
    su, sv = width_mm / (x1 - x0), height_mm / (y1 - y0)
    contours, hierarchy = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy.reshape(-1, 4)

    def polygon(contour):
        if cv2.contourArea(contour) < MIN_AREA_PX:
            return None
        tol = tol_px
        approx = cv2.approxPolyDP(contour, tol, True)
        while len(approx) > MAX_POINTS:
            tol *= 1.6
            approx = cv2.approxPolyDP(contour, tol, True)
        if len(approx) < 3:
            return None
        pts = approx.reshape(-1, 2).astype(float)
        return [(round(float((px - x0) * su), 3), round(float((y1 - py) * sv), 3)) for px, py in pts]

    result = []
    for index, contour in enumerate(contours):
        if hierarchy[index][3] != -1:      # a hole: handled with its parent
            continue
        outer = polygon(contour)
        if outer is None:
            continue
        holes = []
        child = hierarchy[index][2]
        while child != -1:
            hole = polygon(contours[child])
            if hole is not None:
                holes.append(hole)
            child = hierarchy[child][0]
        result.append((outer, holes))
    return result


def _pts(points) -> str:
    return "[" + ", ".join(f"({u:g}, {v:g})" for u, v in points) + "]"


def view_outlines(views: dict[str, Path], bbox: dict, tol_px: float = 0.75) -> dict[str, list]:
    """Polygons of every hull view that exists, keyed by view name."""
    out = {}
    for name, (_, right, up, _) in HULL_VIEWS.items():
        path = views.get(name)
        if path is None or not Path(path).is_file():
            continue
        polygons = mask_polygons(view_mask(Path(path)), float(bbox[right]), float(bbox[up]), tol_px)
        if polygons:
            out[name] = polygons
    return out


def hull_script(outlines: dict[str, list], bbox: dict, use: tuple[str, ...]) -> str | None:
    """Script that intersects the extruded outlines of the views in `use`; None if one of them is missing."""
    if not use or any(name not in outlines for name in use):
        return None
    x, y, z = (float(bbox[k]) for k in "xyz")
    lines = [f"# silhouette hull from the {', '.join(use)} view{'s' if len(use) > 1 else ''}: every outline "
             "extruded through the envelope, then intersected",
             f"X, Y, Z = {x:g}, {y:g}, {z:g}"]
    for name in use:
        plane, _, _, along = HULL_VIEWS[name]
        depth = along.upper()
        first = True
        for outer, holes in outlines[name]:
            solid = f"prism('{plane}', {_pts(outer)}, 0, {depth})"
            lines.append(f"{name} = {solid}" if first else f"{name} = {name}.union({solid})")
            first = False
            for hole in holes:
                lines.append(f"{name} = {name}.cut(prism('{plane}', {_pts(hole)}, -1, {depth} + 1))")
    lines.append("result = " + use[0] + "".join(f".intersect({name})" for name in use[1:]))
    return "\n".join(lines) + "\n"


def hull_candidates(outlines: dict[str, list], bbox: dict) -> list[tuple[str, str]]:
    """(label, script) from the most to the least constrained combination of views."""
    names = [n for n in HULL_VIEWS if n in outlines]
    combos: list[tuple[str, ...]] = []
    if len(names) == 3:
        combos.append(tuple(names))
    combos += [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
    combos += [(n,) for n in names]
    out = []
    for combo in combos:
        script = hull_script(outlines, bbox, combo)
        if script:
            out.append(("+".join(combo), script))
    return out


# -- hull of a turned part --------------------------------------------------------------------------

# axis of revolution -> (view that looks along it, the two views that show it in profile with the screen
# direction the axis runs along in each: "u" = to the right, "v" = up)
REVOLVE_VIEWS = {
    "z": ("top", (("front", "v"), ("right", "v"))),
    "y": ("front", (("top", "v"), ("right", "u"))),
    "x": ("right", (("front", "u"), ("top", "u"))),
}
DISC_IOU = 0.985        # the outline seen along the axis must fill its inscribed circle this well
ROUND_TOL = 0.015       # extents across the axis may differ by this much, relative
PROFILE_TOL = 0.02      # the four half-profiles may differ by this fraction of the diameter (95th percentile)
PROFILE_SAMPLES = 1024


def _cropped(mask: np.ndarray) -> np.ndarray | None:
    ys, xs = np.where(mask)
    if not len(xs) or xs.max() == xs.min() or ys.max() == ys.min():
        return None
    return mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _is_disc(mask: np.ndarray) -> bool:
    """True when the outline of a view (openings filled) is the circle inscribed in its pixel box."""
    import cv2

    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros(mask.shape, dtype=np.uint8)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    h, w = mask.shape
    yy, xx = np.mgrid[0:h, 0:w]
    disc = ((xx - (w - 1) / 2) / (w / 2)) ** 2 + ((yy - (h - 1) / 2) / (h / 2)) ** 2 <= 1.0
    union = np.logical_or(disc, filled > 0).sum()
    return bool(union) and np.logical_and(disc, filled > 0).sum() / union >= DISC_IOU


def _half_profiles(mask: np.ndarray, along: str, across_mm: float) -> tuple[np.ndarray, np.ndarray]:
    """Distance in mm from the centre line to the outline on either side of it, at PROFILE_SAMPLES positions
    from the low to the high end of the axis, which runs up ("v") or to the right ("u") in this view."""
    m = mask[::-1].T if along == "v" else mask      # rows: across the axis; columns: along it, low to high
    m = m if along == "v" else m[::-1]
    n_across, n_along = m.shape
    scale = across_mm / (n_across - 1)
    any_ = m.any(axis=0)
    low = np.where(any_, m.argmax(axis=0), (n_across - 1) / 2)
    high = np.where(any_, n_across - 1 - m[::-1].argmax(axis=0), (n_across - 1) / 2)
    centre = (n_across - 1) / 2
    at = np.linspace(0, n_along - 1, PROFILE_SAMPLES)
    columns = np.arange(n_along)
    return (np.interp(at, columns, (centre - low) * scale), np.interp(at, columns, (high - centre) * scale))


def revolve_profile(views: dict[str, Path], bbox: dict, axis: str, tol_px: float = 0.75):
    """(position along `axis`, radius) polygon of the solid of revolution whose outlines are the views', or
    None when the views do not show one: the view along the axis must be a disc and the two views across it
    must show the same outline, symmetric about the axis."""
    import cv2

    end_view, side_views = REVOLVE_VIEWS[axis]
    others = [k for k in "xyz" if k != axis]
    try:
        length, d1, d2 = float(bbox[axis]), float(bbox[others[0]]), float(bbox[others[1]])
    except (KeyError, TypeError, ValueError):
        return None
    if min(length, d1, d2) <= 0 or abs(d1 - d2) > ROUND_TOL * max(d1, d2):
        return None
    paths = [views.get(name) for name in (end_view, side_views[0][0], side_views[1][0])]
    if any(path is None or not Path(path).is_file() for path in paths):
        return None
    masks = [_cropped(view_mask(Path(path))) for path in paths]
    if any(mask is None for mask in masks) or not _is_disc(masks[0]):
        return None
    diameter = (d1 + d2) / 2
    halves = []
    for mask, (_, along) in zip(masks[1:], side_views):
        halves += _half_profiles(mask, along, diameter)
    halves = np.array(halves)
    if np.percentile(halves.max(axis=0) - halves.min(axis=0), 95) > PROFILE_TOL * diameter:
        return None
    radius = np.clip(halves.mean(axis=0), 0.0, diameter / 2)
    if radius.min() <= 0:
        return None
    position = np.linspace(0.0, length, PROFILE_SAMPLES)
    px = length / (max(masks[1].shape) - 1)          # about one pixel in mm
    curve = np.stack([position, radius], axis=1).astype(np.float32).reshape(-1, 1, 2)
    points = cv2.approxPolyDP(curve, tol_px * px, False).reshape(-1, 2)
    profile = [(0.0, 0.0)] + [(round(float(a), 3), round(float(r), 3)) for a, r in points] + [(length, 0.0)]
    return profile if len(profile) >= 4 else None


def revolve_script(profile: list, outlines: dict[str, list], bbox: dict, axis: str) -> str:
    """Script of the revolved hull: the profile turned about the axis through the middle of the envelope, with
    every opening that an outline shows cut through it."""
    x, y, z = (float(bbox[k]) for k in "xyz")
    c1, c2 = (float(bbox[k]) / 2 for k in "xyz" if k != axis)
    lines = [f"# hull of a turned part: the outline is a circle along {axis.upper()} and the same symmetric profile "
             "in the two other views, so the profile is revolved instead of intersecting flat extrusions",
             f"X, Y, Z = {x:g}, {y:g}, {z:g}",
             f"result = revolve('{axis}', {_pts(profile)}, {c1:g}, {c2:g})"]
    for name, polygons in outlines.items():
        plane, _, _, along = HULL_VIEWS[name]
        for _, holes in polygons:
            for hole in holes:
                lines.append(f"result = result.cut(prism('{plane}', {_pts(hole)}, -1, {along.upper()} + 1))"
                             f"   # opening in the {name} view")
    return "\n".join(lines) + "\n"


def revolve_candidates(views: dict[str, Path], outlines: dict[str, list], bbox: dict,
                       tol_px: float = 0.75) -> list[tuple[str, str]]:
    """(label, script) for every principal axis the views show the part to be turned about."""
    out = []
    for axis in "zyx":
        profile = revolve_profile(views, bbox, axis, tol_px)
        if profile:
            out.append((f"revolve {axis}", revolve_script(profile, outlines, bbox, axis)))
    return out


# -- features straight from the facts sheet --------------------------------------------------------

def _axis_vector(axis: str):
    """'X' / 'Y' / 'Z' -> that letter; '(-0.29, -0.66, 0.70)' -> a unit vector; anything else -> None."""
    text = str(axis).strip()
    if text.upper() in ("X", "Y", "Z"):
        return text.lower()
    try:
        values = [float(v) for v in text.strip("()").split(",")]
    except ValueError:
        return None
    if len(values) != 3:
        return None
    norm = float(np.linalg.norm(values))
    return tuple(v / norm for v in values) if norm > 1e-9 else None


def bore_cuts(facts: dict) -> list[tuple[str, str]]:
    """(label, cutter expression) for every hole/bore the facts sheet locates: one cylinder per centre,
    as long as the cylindrical face is, placed where the facts say it is."""
    cuts = []
    for item in facts.get("cylindrical_holes_and_bores") or []:
        try:
            diameter = float(item["diameter"])
            centres = item.get("centres") or []
            lengths = [float(v) for v in item.get("lengths") or []]
        except (KeyError, TypeError, ValueError):
            continue
        axis = _axis_vector(item.get("axis", ""))
        if axis is None or diameter <= 0 or not lengths:
            continue
        for index, centre in enumerate(centres):
            try:
                cx, cy, cz = (float(v) for v in centre)
            except (TypeError, ValueError):
                continue
            length = lengths[index] if len(lengths) == len(centres) else max(lengths)
            half = length / 2
            label = f"bore d={diameter:g} axis {item.get('axis')} at ({cx:g}, {cy:g}, {cz:g}) length {length:g}"
            if isinstance(axis, str):
                along = {"x": cx, "y": cy, "z": cz}[axis]
                c1, c2 = [v for k, v in zip("xyz", (cx, cy, cz)) if k != axis]
                cuts.append((label, f"cyl('{axis}', {diameter:g}, {along - half:g}, {along + half:g}, {c1:g}, {c2:g})"))
            else:
                p0 = tuple(round(c - half * a, 3) for c, a in zip((cx, cy, cz), axis))
                p1 = tuple(round(c + half * a, 3) for c, a in zip((cx, cy, cz), axis))
                cuts.append((label, f"cyl_between({diameter:g}, {p0}, {p1})"))
    return cuts


# -- is a hole see-through? ---------------------------------------------------------------------------

# axis of the hole -> views that look along it: (view name, axis shown to the right, +1 if it grows to the
# right else -1, axis shown up, +1 if it grows upwards else -1)
_ALONG = {
    "x": [("right", "y", 1, "z", 1), ("left", "y", -1, "z", 1)],
    "y": [("front", "x", 1, "z", 1), ("back", "x", -1, "z", 1)],
    "z": [("top", "x", 1, "y", 1), ("bottom", "x", 1, "y", -1)],
}


def daylight(views: dict[str, Path], bbox: dict, axis: str, centre, diameter: float, part: float = 0.6) -> float | None:
    """Largest share of background pixels, over the views that look along `axis`, inside the disc of
    `part` x diameter round the hole's centre. None when no such view exists."""
    axis = str(axis).lower()
    answers = []
    for name, right, right_sign, up, up_sign in _ALONG.get(axis, []):
        path = views.get(name)
        if path is None or not Path(path).is_file():
            continue
        mask = view_mask(Path(path))
        ys, xs = np.where(mask)
        if not len(xs):
            continue
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        width, height = float(bbox[right]), float(bbox[up])
        if width <= 0 or height <= 0:
            continue
        u, v = float(centre["xyz".index(right)]), float(centre["xyz".index(up)])
        fu = u / width if right_sign > 0 else 1.0 - u / width
        fv = v / height if up_sign > 0 else 1.0 - v / height
        px, py = x0 + fu * (x1 - x0), y1 - fv * (y1 - y0)
        radius = max(1.5, 0.5 * part * diameter * ((x1 - x0) / width + (y1 - y0) / height) / 2)
        yy, xx = np.ogrid[:mask.shape[0], :mask.shape[1]]
        disc = (xx - px) ** 2 + (yy - py) ** 2 <= radius ** 2
        if not disc.any():
            continue
        answers.append(float((~mask[disc]).mean()))
    return max(answers) if answers else None


def see_through(views: dict[str, Path], bbox: dict, axis: str, centre, diameter: float) -> bool | None:
    """Whether the reference views show daylight through a hole along a principal axis: True when the middle of
    the hole is background in a view that looks along it, False when it is part, None when no such view exists.

    A drilled hole that the brief calls "through" but that ends in a drill point (or is covered by material
    further along) is part-coloured in the view; cutting it through the whole part would open a passage the
    part does not have."""
    share = daylight(views, bbox, axis, centre, diameter)
    return None if share is None else share >= 0.6
