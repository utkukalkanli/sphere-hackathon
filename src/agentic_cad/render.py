"""Dependency-light rendering of a triangle mesh (numpy + Pillow, no OpenGL).

Used for the two things the feedback loop needs:
  * silhouette masks drawn in the pixel frame of the reference views -> per-view IoU
  * shaded tiles laid out next to the reference views -> one image the VLM can compare
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# name, direction the camera looks along, up hint, (axis shown to the right, axis shown up)
VIEWS = [
    ("front", (0, 1, 0), (0, 0, 1), ("x", "z")),
    ("back", (0, -1, 0), (0, 0, 1), ("x", "z")),
    ("left", (1, 0, 0), (0, 0, 1), ("y", "z")),
    ("right", (-1, 0, 0), (0, 0, 1), ("y", "z")),
    ("top", (0, 0, -1), (0, 1, 0), ("x", "y")),
    ("bottom", (0, 0, 1), (0, -1, 0), ("x", "y")),
    ("iso_front_right_top", (-1, 1, -1), (0, 0, 1), None),
    ("iso_back_left_bottom", (1, -1, 1), (0, 0, 1), None),
]
VIEW_NAMES = [v[0] for v in VIEWS]
ORTHO_VIEWS = [v[0] for v in VIEWS if v[3]]
_AXIS = {"x": 0, "y": 1, "z": 2}
BASE_COLOR = np.array([205, 163, 108], dtype=float)


def view_basis(direction, up_hint):
    d = np.asarray(direction, dtype=float)
    d /= np.linalg.norm(d)
    right = np.cross(d, np.asarray(up_hint, dtype=float))
    right /= np.linalg.norm(right)
    up = np.cross(right, d)
    return d, right, up


def reference_mask(image_path: Path, size: int) -> np.ndarray:
    """Part pixels of a reference view (rendered on a white background)."""
    img = Image.open(image_path).convert("RGB").resize((size, size), Image.BILINEAR)
    return np.asarray(img, dtype=np.int32).sum(axis=2) < 735


def _draw_mask(xy: np.ndarray, size: int) -> np.ndarray:
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    for tri in xy:
        draw.polygon([tuple(tri[0]), tuple(tri[1]), tuple(tri[2])], fill=255, outline=255)
    return np.asarray(img) > 0


def silhouette_iou(verts, tris, view, ref_mask: np.ndarray, target_bbox) -> float | None:
    """IoU between the candidate's silhouette and a reference orthographic view.

    Both the candidate and the target sit with their bounding-box min corner at the origin. The
    reference mask's pixel box corresponds to the target extents in that view, which gives the
    mm->px scale, so a candidate of the wrong size or proportion scores low."""
    name, direction, up_hint, axes = view
    if axes is None or not ref_mask.any():
        return None
    size = ref_mask.shape[0]
    ys, xs = np.where(ref_mask)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    width_mm, height_mm = target_bbox[_AXIS[axes[0]]], target_bbox[_AXIS[axes[1]]]
    if width_mm <= 0 or height_mm <= 0:
        return None
    scale = ((x1 - x0) / width_mm + (y1 - y0) / height_mm) / 2
    _, right, up = view_basis(direction, up_hint)
    # where the target box starts along the screen axes (negative when the axis points left/down)
    u_min = min(0.0, float(right[_AXIS[axes[0]]]) * width_mm)
    v_min = min(0.0, float(up[_AXIS[axes[1]]]) * height_mm)
    px = x0 + (verts @ right - u_min) * scale
    py = y1 - (verts @ up - v_min) * scale
    xy = np.stack([px, py], axis=1)[tris]
    cand = _draw_mask(xy, size)
    union = np.logical_or(cand, ref_mask).sum()
    return float(np.logical_and(cand, ref_mask).sum() / union) if union else None


def shaded_tile(verts, tris, view, size: int) -> Image.Image:
    """Flat-shaded orthographic picture of the mesh, painter's algorithm, fitted to the tile."""
    _, direction, up_hint, _ = view
    d, right, up = view_basis(direction, up_hint)
    u, v, depth = verts @ right, verts @ up, verts @ d
    span = max(u.max() - u.min(), v.max() - v.min(), 1e-9)
    scale = size * 0.94 / span
    px = size / 2 + (u - (u.max() + u.min()) / 2) * scale
    py = size / 2 - (v - (v.max() + v.min()) / 2) * scale
    p = verts[tris]
    normals = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    keep = lengths > 1e-12
    normals[keep] /= lengths[keep][:, None]
    light = -d + 0.45 * up - 0.3 * right
    light /= np.linalg.norm(light)
    tri_depth = depth[tris].mean(axis=1)
    # darken with distance so steps between parallel faces stay visible
    fade = 1.0 - 0.28 * (tri_depth - depth.min()) / max(depth.max() - depth.min(), 1e-9)
    shade = (0.38 + 0.62 * np.abs(normals @ light)) * fade
    order = np.argsort(-tri_depth)  # far triangles first
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    xy = np.stack([px, py], axis=1)[tris]
    for i in order:
        if not keep[i]:
            continue
        color = tuple(int(c) for c in np.clip(BASE_COLOR * shade[i], 0, 255))
        draw.polygon([tuple(xy[i, 0]), tuple(xy[i, 1]), tuple(xy[i, 2])], fill=color, outline=color)
    return img


def comparison_sheets(verts, tris, ref_images: dict[str, Path], tile: int, out_dir: Path, stem: str) -> list[Path]:
    """Two images (views 0-3 and 4-7): reference views on the top row, the candidate below."""
    sheets = []
    label_h = 18
    for part, views in enumerate((VIEWS[:4], VIEWS[4:])):
        sheet = Image.new("RGB", (tile * len(views), 2 * (tile + label_h)), "white")
        draw = ImageDraw.Draw(sheet)
        for col, view in enumerate(views):
            name = view[0]
            x = col * tile
            draw.text((x + 4, 3), f"TARGET {name}", fill=(0, 0, 0))
            ref = ref_images.get(name)
            if ref is not None and Path(ref).is_file():
                sheet.paste(Image.open(ref).convert("RGB").resize((tile, tile), Image.LANCZOS), (x, label_h))
            draw.text((x + 4, tile + label_h + 3), f"YOUR MODEL {name}", fill=(170, 0, 0))
            sheet.paste(shaded_tile(verts, tris, view, tile), (x, tile + 2 * label_h))
            draw.line([(x, 0), (x, sheet.height)], fill=(180, 180, 180))
        draw.line([(0, tile + label_h), (sheet.width, tile + label_h)], fill=(120, 120, 120))
        path = out_dir / f"{stem}_views_{part + 1}.png"
        sheet.save(path)
        sheets.append(path)
    return sheets
