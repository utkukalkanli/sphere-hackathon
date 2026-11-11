"""The eight renders of a test case as cameras: project a solid into every view and compare silhouettes.

Used to check cuts that the silhouettes cannot make themselves (holes, pockets, recesses): such a cut
must never make a view show background where the render shows the part. Also draws the part's
coordinate system on the isometric renders, for the dashboard and for a model to read positions from.

Conventions (README), with the bounding box's min corner at the origin:
  front looks along +Y (X right, Z up)    back   along -Y (X left, Z up)
  left  looks along +X (Y left, Z up)     right  along -X (Y right, Z up)
  top   looks along -Z (X right, Y up)    bottom along +Z (X right, Y down)
  iso_front_right_top from (+X, -Y, +Z), iso_back_left_bottom from (-X, +Y, -Z), Z up in both.
Orthographic renders are framed on the part, so their cropped silhouette is the box face; the
isometric ones are centred on the box and framed on the box or on the part (see `IsoCamera.fit`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import visual_hull as vh

# view -> (axis index or None, flip) for the horizontal and the vertical image direction
ORTHO_VIEWS = {
    "front": ((0, False), (2, False)),
    "back": ((0, True), (2, False)),
    "left": ((1, True), (2, False)),
    "right": ((1, False), (2, False)),
    "top": ((0, False), (1, False)),
    "bottom": ((0, False), (1, True)),
}
ISO_VIEWS = {"iso_front_right_top": vh.ISO_RIGHT, "iso_back_left_bottom": -vh.ISO_RIGHT}
AXIS_COLOURS = {"x": (40, 40, 220), "y": (40, 160, 40), "z": (220, 90, 30)}  # BGR


class OrthoCamera:
    def __init__(self, view: str, dims: np.ndarray, shape: tuple[int, int]):
        (self.u, self.flip_u), (self.v, self.flip_v) = ORTHO_VIEWS[view]
        self.dims, (self.height, self.width) = dims, shape

    def __call__(self, points: np.ndarray) -> np.ndarray:
        p = np.asarray(points, float)
        u, v = p[..., self.u], p[..., self.v]
        if self.flip_u:
            u = self.dims[self.u] - u
        if self.flip_v:
            v = self.dims[self.v] - v
        return np.stack([u / self.dims[self.u] * (self.width - 1),
                         (self.height - 1) - v / self.dims[self.v] * (self.height - 1)], -1)


class IsoCamera:
    def __init__(self, right: np.ndarray, dims: np.ndarray, size: int, mm_per_px: float):
        self.right, self.up = right, vh.ISO_UP
        self.dims, self.centre, self.half, self.mm_per_px = dims, dims / 2, size / 2, mm_per_px

    def __call__(self, points: np.ndarray) -> np.ndarray:
        p = np.asarray(points, float) - self.centre
        a, b = p @ self.right, p @ self.up
        return np.stack([a / self.mm_per_px + self.half - 0.5, self.half - 0.5 - b / self.mm_per_px], -1)

    @classmethod
    def fit(cls, right: np.ndarray, dims: np.ndarray, render: np.ndarray, tris: np.ndarray) -> "IsoCamera":
        """The renderer frames either the bounding box or the part itself: keep whichever fits `tris` better."""
        size, best = render.shape[0], None
        corners = np.array([[x, y, z] for x in (0, dims[0]) for y in (0, dims[1]) for z in (0, dims[2])])
        for points in (corners, tris.reshape(-1, 3)):
            centred = points - dims / 2
            half_mm = max(np.abs(centred @ right).max(), np.abs(centred @ vh.ISO_UP).max())
            camera = cls(right, dims, size, half_mm * vh.FRAME_MARGIN / (size / 2))
            score = iou(rasterize(tris, camera, render.shape), render)
            if best is None or score > best[0]:
                best = (score, camera)
        return best[1]


def rasterize(tris: np.ndarray, camera, shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    pixels = camera(tris).round().astype(np.int32)
    for triangle in pixels:  # one by one: fillPoly's even-odd rule would cancel overlapping triangles
        cv2.fillConvexPoly(mask, triangle, 255)
    return mask > 0


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / max((a | b).sum(), 1))


def mesh(shape: TopoDS_Shape, dims: np.ndarray) -> np.ndarray:
    return vh.triangles(shape, 0.003 * float(np.linalg.norm(dims)))


@dataclass
class View:
    name: str
    path: Path
    render: np.ndarray  # bool mask of the part as rendered
    camera: object


class ViewSet:
    """All renders of a case with their cameras; the iso scale is fitted once on `reference`."""

    def __init__(self, views: list[Path], dims: tuple[float, float, float], reference: TopoDS_Shape):
        self.dims = np.array(dims, float)
        self.views: list[View] = []
        tris = None
        for name in ORTHO_VIEWS:
            path = vh.find_view(views, name)
            render = vh.part_mask(path) if path else None
            if render is not None:
                self.views.append(View(name, path, render, OrthoCamera(name, self.dims, render.shape)))
        for name, right in ISO_VIEWS.items():
            path = vh.find_view(views, name)
            render = vh.full_mask(path) if path else None
            if render is None or render.shape[0] != render.shape[1]:
                continue
            tris = mesh(reference, self.dims) if tris is None else tris
            self.views.append(View(name, path, render, IsoCamera.fit(right, self.dims, render, tris)))

    def missing(self, shape: TopoDS_Shape) -> dict[str, int]:
        """Per view, how many pixels the render shows as part but `shape` leaves empty. The render is
        shrunk by a pixel first: along its outlines anti-aliasing decides, not the geometry."""
        tris = mesh(shape, self.dims)
        return {v.name: int((self._core(v) & ~rasterize(tris, v.camera, v.render.shape)).sum()) for v in self.views}

    @staticmethod
    def _core(view: View) -> np.ndarray:
        if not hasattr(view, "core"):
            view.core = cv2.erode(view.render.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2) > 0
        return view.core

    def tolerance(self, view: str) -> int:
        """How many more missing pixels a cut may cause in a view: anti-aliasing and meshing noise only."""
        render = next(v.render for v in self.views if v.name == view)
        return max(25, int(2e-4 * render.sum()))

    def iso_overlay(self, shape: TopoDS_Shape | None = None) -> dict[str, np.ndarray]:
        """The iso renders with the coordinate system drawn on, and where `shape` differs from them."""
        tris = mesh(shape, self.dims) if shape is not None else None
        images = {}
        for v in self.views:
            if not isinstance(v.camera, IsoCamera):
                continue
            image = cv2.imread(str(v.path))
            if tris is not None:
                ours = rasterize(tris, v.camera, v.render.shape)
                overlay = image.copy()
                overlay[ours & ~v.render] = (40, 40, 230)   # excess: still to cut
                overlay[v.render & ~ours] = (230, 120, 30)  # missing: cut too much
                image = cv2.addWeighted(overlay, 0.55, image, 0.45, 0)
            draw_frame(image, v.camera, self.dims)
            images[v.name] = image
        return images


def nice_step(length: float) -> float:
    raw = length / 6
    power = 10 ** np.floor(np.log10(raw))
    return float(min((s * power for s in (1, 2, 5, 10)), key=lambda s: abs(s - raw)))


def draw_frame(image: np.ndarray, camera, dims: np.ndarray) -> None:
    """Bounding box edges, and X (red), Y (green), Z (blue) axes from the min corner, ticked in mm."""
    corners = {(i, j, k): np.array([i * dims[0], j * dims[1], k * dims[2]])
               for i in (0, 1) for j in (0, 1) for k in (0, 1)}
    for a, pa in corners.items():
        for b, pb in corners.items():
            if a < b and sum(x != y for x, y in zip(a, b)) == 1:
                p, q = camera(np.array([pa, pb])).round().astype(int)
                cv2.line(image, tuple(map(int, p)), tuple(map(int, q)), (150, 150, 150), 1, cv2.LINE_AA)
    origin = camera(np.zeros((1, 3)))[0].round().astype(int)
    for index, name in enumerate("xyz"):
        unit = np.eye(3)[index]
        end = camera(unit[None] * dims[index] * 1.12)[0].round().astype(int)
        colour = AXIS_COLOURS[name]
        cv2.arrowedLine(image, tuple(map(int, origin)), tuple(map(int, end)), colour, 2, cv2.LINE_AA, tipLength=0.04)
        cv2.putText(image, name.upper(), tuple(map(int, end + 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
        step = nice_step(dims[index])
        for value in np.arange(step, dims[index] + 1e-6, step):
            p = camera(unit[None] * value)[0].round().astype(int)
            cv2.circle(image, tuple(map(int, p)), 3, colour, -1)
            cv2.putText(image, f"{value:g}", tuple(map(int, p + np.array([4, -4]))), cv2.FONT_HERSHEY_SIMPLEX,
                        0.35, colour, 1, cv2.LINE_AA)
    cv2.circle(image, tuple(map(int, origin)), 4, (0, 0, 0), -1)
