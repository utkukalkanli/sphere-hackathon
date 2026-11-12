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
