"""The lines in the renders, and the same lines drawn from a candidate solid.

The renders draw every face boundary as a thin dark line. A line inside the silhouette that our solid
does not have marks a face we have not cut yet (a pocket, a step, a recess); a line our solid has and
the render does not marks a cut that is wrong. `LineViews.mismatch` counts both, in all eight views,
so candidate cuts can be compared: the right one makes the drawings agree.

Our lines come from OpenCascade's hidden-line removal (`HLRBRep_PolyAlgo`) in each view's projection,
plus the outline of our solid's silhouette. Faces on the same surface are merged first (`vh.tidy`),
as the renderer shows no line between them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GeomAbs import GeomAbs_Line
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from agentic_cad import visual_hull as vh
from agentic_cad.views import ORTHO_VIEWS, IsoCamera, OrthoCamera, rasterize

LINE_RATIO = 0.85    # a line pixel is at most this share of the brightness of its 5 px neighbourhood
LINE_MIN_STEP = 5    # ... and at least this many grey levels darker (lines between dark faces are faint)
TOLERANCE_PX = 3     # lines closer than this count as the same line
# where each view looks (unit vector from the camera into the scene)
ORTHO_DIRECTIONS = {"front": (0, 1, 0), "back": (0, -1, 0), "left": (1, 0, 0), "right": (-1, 0, 0),
                    "top": (0, 0, -1), "bottom": (0, 0, 1)}


def ortho_basis(name: str) -> tuple[np.ndarray, np.ndarray]:
    """Unit vectors of the image's right and up direction in a straight view (see `views.ORTHO_VIEWS`)."""
    (u, flip_u), (v, flip_v) = ORTHO_VIEWS[name]
    right, up = np.zeros(3), np.zeros(3)
    right[u], up[v] = (-1.0 if flip_u else 1.0), (-1.0 if flip_v else 1.0)
    return right, up


def render_lines(image_path: Path, crop: bool) -> np.ndarray | None:
    """Line pixels of a render: thin dark strokes, and the outline of the part. Ortho views are cropped
    to the part's rectangle, like `vh.part_mask`."""
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        return None
    grey = image.min(axis=2)
    part = grey <= vh.BACKGROUND_MIN
    if not part.any():
        return None
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    closed = cv2.morphologyEx(grey, cv2.MORPH_CLOSE, kernel).astype(np.int16)
    strokes = (grey < LINE_RATIO * closed) & (closed - grey > LINE_MIN_STEP)
    outline = part & ~cv2.erode(part.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    lines = (strokes & cv2.dilate(part.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)) | outline
    if crop:
        rows, cols = np.where(part.any(axis=1))[0], np.where(part.any(axis=0))[0]
        lines = lines[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    return lines


def visible_edges(shape: TopoDS_Shape, right: np.ndarray, up: np.ndarray, deflection: float,
                  smooth: bool = True) -> list[np.ndarray]:
    """The edges of `shape` seen when looking along -(right x up), hidden parts removed, as polylines of
    (a, b) = (p . right, p . up) in mm. Sharp edges always, tangent ones (fillet boundaries) if `smooth`."""
    BRepMesh_IncrementalMesh(shape, deflection)
    toward_viewer = np.cross(right, up)
    algo = HLRBRep_PolyAlgo()
    algo.Load(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*map(float, toward_viewer)),
                                            gp_Dir(*map(float, right)))))
    algo.Update()
    to_shape = HLRBRep_PolyHLRToShape()
    to_shape.Update(algo)
    compounds = [to_shape.VCompound()] + ([to_shape.Rg1LineVCompound()] if smooth else [])
    polylines = []
    for compound in compounds:
        if compound is None or compound.IsNull():
            continue
        explorer = TopExp_Explorer(compound, TopAbs_EDGE)
        while explorer.More():
            edge = TopoDS.Edge(explorer.Current())
            curve = BRepAdaptor_Curve(edge)
            first, last = curve.FirstParameter(), curve.LastParameter()
            n = 2 if curve.GetType() == GeomAbs_Line else 12
            points = [curve.Value(first + (last - first) * i / (n - 1)) for i in range(n)]
            polylines.append(np.array([(p.X(), p.Y()) for p in points]))
            explorer.Next()
    return polylines


def draw(polylines: list[np.ndarray], camera, right: np.ndarray, up: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Polylines in projection coordinates as 1 px lines in a view's image."""
    image = np.zeros(size, np.uint8)
    for line in polylines:
        points3d = line[:, :1] * right + line[:, 1:2] * up
        if isinstance(camera, IsoCamera):  # the iso camera projects relative to the box centre
            points3d = points3d + camera.centre - (camera.centre @ right) * right - (camera.centre @ up) * up
        pixels = camera(points3d).round().astype(np.int32)
        cv2.polylines(image, [pixels], False, 1, 1)
    return image > 0


@dataclass
class LineView:
    name: str
    lines: np.ndarray        # render lines
    near: np.ndarray         # render lines grown by TOLERANCE_PX
    camera: object
    right: np.ndarray
    up: np.ndarray


class LineViews:
    """The line drawings of all eight renders, with cameras that put our solid onto them."""

    def __init__(self, views: list[Path], dims, cameras: dict):
        self.dims = np.array(dims, float)
        self.views: list[LineView] = []
        grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * TOLERANCE_PX + 1,) * 2)
        for name, camera in cameras.items():
            path = vh.find_view(views, name)
            lines = render_lines(path, crop=name in ORTHO_VIEWS) if path else None
            if lines is None:
                continue
            if name in ORTHO_VIEWS:
                camera = OrthoCamera(name, self.dims, lines.shape)
                right, up = ortho_basis(name)
            else:
                right, up = camera.right, camera.up
            near = cv2.dilate(lines.astype(np.uint8), grow).astype(bool)
            self.views.append(LineView(name, lines, near, camera, right, up))

    def drawn(self, shape: TopoDS_Shape) -> dict[str, np.ndarray]:
        """Our solid's lines in every view: visible edges and the outline of its silhouette."""
        tidy = vh.tidy(shape)
        deflection = 0.002 * float(np.linalg.norm(self.dims))
        tris = vh.triangles(tidy, deflection)
        result = {}
        for v in self.views:
            try:
                polylines = visible_edges(tidy, v.right, v.up, deflection)
            except Exception:  # HLRBRep_PolyAlgo fails on some meshes (Standard_OutOfRange): mesh finer once
                polylines = visible_edges(tidy, v.right, v.up, deflection * 0.6)
            lines = draw(polylines, v.camera, v.right, v.up, v.lines.shape)
            silhouette = np.pad(rasterize(tris, v.camera, v.lines.shape), 1)
            outline = silhouette & ~cv2.erode(silhouette.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            result[v.name] = lines | outline[1:-1, 1:-1]
        return result

    def mismatch(self, shape: TopoDS_Shape, views: set[str] | None = None) -> dict[str, tuple[int, int]]:
        """Per view: (render line pixels we have no line near, our line pixels the render has no line near)."""
        grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * TOLERANCE_PX + 1,) * 2)
        result = {}
        for v, ours in zip(self.views, self.drawn(shape).values()):
            if views and v.name not in views:
                continue
            ours_near = cv2.dilate(ours.astype(np.uint8), grow).astype(bool)
            result[v.name] = (int((v.lines & ~ours_near).sum()), int((ours & ~v.near).sum()))
        return result


def cameras_for(view_set) -> dict:
    """The cameras of a `views.ViewSet` by view name (ortho ones are rebuilt per line image)."""
    return {v.name: v.camera for v in view_set.views}
