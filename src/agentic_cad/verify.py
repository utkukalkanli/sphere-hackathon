"""Checks a candidate solid against the inputs of a case. Needs no ground truth and no model.

The candidate is drawn from the eight cameras of the views (a depth buffer over its tessellation,
numpy only) and compared with what the views show:

- silhouette: the part's mask in the view against the candidate's;
- lines: the views draw a dark line on every face boundary. Every sharp edge the candidate shows
  should lie on such a line (`precision`), and every line should be some face boundary of the
  candidate (`recall`);
- volume and surface area against facts.json.

`check(solid, case_dir)` returns a `Report`; `Report.score` is one number in 0..1 for comparing two
candidates of the same part.

Frame: as in visual_hull, the solid has its bounding-box min corner at the origin. A pixel is
(column, row); the image centre is the projected centre of the bounding box.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape

from agentic_cad.visual_hull import BACKGROUND_MIN, FRAME_MARGIN, find_view

SIZE = 1024            # the views are 1024 px; candidates are drawn at the same size
LINE_CONTRAST = 28     # a line is at least this much darker than its surroundings (0..255)
TOLERANCE_PX = 2.5     # a line and an edge this close are the same
CREASE_DEG = 18.0      # two faces meeting at a flatter angle do not make a sharp edge
DEPTH_JUMP_PX = 3.0    # neighbouring pixels further apart in depth than this are an occlusion edge
LARGE_TRIANGLE_PX = 1024  # a triangle whose rectangle has this many pixels is drawn by itself

# name -> (direction from the part towards the camera, up), as in the README
CAMERAS = {
    "front": ((0, -1, 0), (0, 0, 1)),
    "back": ((0, 1, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, 0, 1)),
    "right": ((1, 0, 0), (0, 0, 1)),
    "top": ((0, 0, 1), (0, 1, 0)),
    "bottom": ((0, 0, -1), (0, -1, 0)),
    "iso_front_right_top": ((1, -1, 1), (0, 0, 1)),
    "iso_back_left_bottom": ((-1, 1, -1), (0, 0, 1)),
}
ORTHO_VIEWS = ("front", "back", "left", "right", "top", "bottom")


def camera(name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Unit vectors of the image's right and up direction and the direction towards the camera."""
    towards, up = (np.array(v, dtype=float) for v in CAMERAS[name])
    towards /= np.linalg.norm(towards)
    right = np.cross(up, towards)
    right /= np.linalg.norm(right)
    return right, np.cross(towards, right), towards


# -- what a view shows -----------------------------------------------------------------------------

@dataclass
class Observed:
    name: str
    mask: np.ndarray      # bool, the part
    lines: np.ndarray     # bool, the dark lines on face boundaries (and around the part)
    mm_per_px: float
    image: np.ndarray     # BGR


def _read(path: Path) -> np.ndarray | None:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:  # transparent counts as background
        alpha = image[:, :, 3:4] > 127
        image = np.where(alpha, image[:, :, :3], 255).astype(np.uint8)
    return image


def find_lines(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """The thin dark lines of a view. A line is darker than everything a few pixels around it, however
    dark the face it lies on, so it is found as what a small closing fills in."""
    bright = image.max(axis=2)  # the part is tan: its brightest channel separates it best from black
    closed = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    lines = (closed.astype(np.int16) - bright) >= LINE_CONTRAST
    # the outline of the part against the white background is a line too, whatever its contrast
    outline = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    return (lines & cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)) | outline


def observe(case_dir: Path, dims: dict[str, float] | None = None) -> dict[str, Observed]:
    """Mask, lines and scale of every view of a case that can be read."""
    if dims is None:
        box = json.loads((case_dir / "facts.json").read_text())["bounding_box_mm"]
        dims = {k: float(box[k]) for k in "xyz"}
    paths = sorted((case_dir / "images").glob("view_*.png"))
    images = {}
    for name in CAMERAS:
        path = find_view(paths, name)
        image = _read(path) if path else None
        if image is not None and image.shape[0] == image.shape[1]:
            images[name] = image
    masks = {name: image.min(axis=2) <= BACKGROUND_MIN for name, image in images.items()}
    size = np.array([dims["x"], dims["y"], dims["z"]])
    result = {}
    for name, image in images.items():
        mask = masks[name]
        if not mask.any():
            continue
        right, up, _ = camera(name)
        if name in ORTHO_VIEWS:  # the frame fits the face of the bounding box the view looks at
            half_mm = max(abs(right) @ size, abs(up) @ size) / 2
            mm_per_px = half_mm * FRAME_MARGIN / (image.shape[0] / 2)
        else:
            mm_per_px = _iso_scale(mask, masks.get("top"), masks.get("bottom"), dims)
            if mm_per_px is None:
                continue
        result[name] = Observed(name, mask, find_lines(image, mask), mm_per_px, image)
    return result


def _iso_scale(iso_mask: np.ndarray, top: np.ndarray | None, bottom: np.ndarray | None,
               dims: dict[str, float]) -> float | None:
    """mm per pixel of an isometric view. Its horizontal direction is (1, 1, 0) / sqrt 2, and how far
    the part reaches along that is known exactly from the top (or bottom) view, whose scale is fixed
    by the bounding box. The width of the part in the isometric view is the same length in pixels."""
    for mask, flip in ((top, False), (bottom, True)):
        if mask is None:
            continue
        rows, cols = np.nonzero(mask)
        if len(rows) < 2:
            continue
        half_px = mask.shape[0] / 2
        scale = max(dims["x"], dims["y"]) / 2 * FRAME_MARGIN / half_px
        x = (cols + 0.5 - half_px) * scale
        y = (half_px - 0.5 - rows) * scale * (-1 if flip else 1)
        along = (x + y) / np.sqrt(2)
        width_mm = float(along.max() - along.min()) + scale  # pixel centres to pixel edges
        iso_cols = np.nonzero(iso_mask.any(axis=0))[0]
        width_px = float(iso_cols[-1] - iso_cols[0] + 1)
        if width_px > 8 and width_mm > 0:
            return width_mm / width_px
    return None


# -- drawing a candidate -----------------------------------------------------------------------------

@dataclass
class Mesh:
    triangles: np.ndarray   # (n, 3, 3) mm
    face: np.ndarray        # (n,) index of the B-rep face each triangle belongs to
    normal: np.ndarray      # (n, 3) unit normals (sign arbitrary)


def tessellate(shape: TopoDS_Shape, deflection: float, angular: float = 0.35) -> Mesh:
    """`angular` (radians) is how far the normals of neighbouring triangles on a curved face may differ."""
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, True)
    triangles, faces = [], []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    index = 0
    while explorer.More():
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(TopoDS.Face(explorer.Current()), location)
        if mesh is not None and mesh.NbTriangles():
            transform = location.Transformation()
            nodes = np.empty((mesh.NbNodes(), 3))
            for i in range(1, mesh.NbNodes() + 1):
                point = mesh.Node(i).Transformed(transform)
                nodes[i - 1] = (point.X(), point.Y(), point.Z())
            corners = np.array([mesh.Triangle(i).Get() for i in range(1, mesh.NbTriangles() + 1)]) - 1
            triangles.append(nodes[corners])
            faces.append(np.full(len(corners), index))
        index += 1
        explorer.Next()
    if not triangles:
        return Mesh(np.zeros((0, 3, 3)), np.zeros(0, int), np.zeros((0, 3)))
    tris = np.concatenate(triangles)
    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(normal, axis=1, keepdims=True)
    return Mesh(tris, np.concatenate(faces), normal / np.maximum(length, 1e-12))


def rasterise(points: np.ndarray, depth: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    """Depth buffer over triangles. `points` (n, 3, 2) are pixel coordinates (column, row), `depth`
    (n, 3) grows towards the camera. Returns, per pixel, the index of the nearest triangle (-1 where
    there is none) and its depth."""
    index_buffer = np.full(size * size, -1, np.int32)
    depth_buffer = np.full(size * size, -np.inf)
    if not len(points):
        return index_buffer.reshape(size, size), depth_buffer.reshape(size, size)
    low = np.clip(np.ceil(points.min(axis=1) - 1e-9), 0, size - 1).astype(np.int64)
    high = np.clip(np.floor(points.max(axis=1) + 1e-9), -1, size - 1).astype(np.int64)
    extent = high - low + 1   # (n, 2): columns, rows
    a, b, c = points[:, 0], points[:, 1], points[:, 2]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    usable = (extent.min(axis=1) > 0) & (np.abs(area) > 1e-12)

    def draw(chosen: np.ndarray, width: int, height: int) -> None:
        """All triangles in `chosen` at once, each tested on a block of width x height pixels."""
        cols = low[chosen, 0][:, None, None] + np.arange(width)[None, None, :]     # (m, 1, w)
        rows = low[chosen, 1][:, None, None] + np.arange(height)[None, :, None]    # (m, h, 1)
        pa, pb, pc, ar = a[chosen], b[chosen], c[chosen], area[chosen]

        def edge(p, q):  # the pixel's barycentric weight for the corner opposite the edge p-q
            return ((q[:, 0] - p[:, 0])[:, None, None] * (rows - p[:, 1][:, None, None])
                    - (q[:, 1] - p[:, 1])[:, None, None] * (cols - p[:, 0][:, None, None])) / ar[:, None, None]

        wc, wa, wb = edge(pa, pb), edge(pb, pc), edge(pc, pa)
        inside = (wa >= -1e-7) & (wb >= -1e-7) & (wc >= -1e-7) & (cols < size) & (rows < size)
        which, r, col = np.nonzero(inside)
        d = depth[chosen]
        z = wa[which, r, col] * d[which, 0] + wb[which, r, col] * d[which, 1] + wc[which, r, col] * d[which, 2]
        pixel = (low[chosen, 1][which] + r) * size + low[chosen, 0][which] + col
        triangle = chosen[which]
        order = np.argsort(z, kind="stable")
        pixel, z, triangle = pixel[order], z[order], triangle[order]
        nearer = z > depth_buffer[pixel]
        pixel, z, triangle = pixel[nearer], z[nearer], triangle[nearer]
        depth_buffer[pixel] = z            # sorted by depth: for a pixel hit twice the nearest is written last
        index_buffer[pixel] = triangle

    def draw_one(i: int) -> None:
        """One large triangle on its own rectangle: the same test and the same depth as `draw`,
        without the padding to a power of two, the sort and the index arrays, which are what a
        check of a blocky solid spent its time on (eval 236: 8 views in 0.12 s instead of 0.69 s)."""
        x0, y0, x1, y1 = low[i, 0], low[i, 1], high[i, 0] + 1, high[i, 1] + 1
        cols = np.arange(x0, x1, dtype=float)[None, :]
        rows = np.arange(y0, y1, dtype=float)[:, None]

        def edge(p, q):
            return ((q[0] - p[0]) * (rows - p[1]) - (q[1] - p[1]) * (cols - p[0])) / area[i]

        wc, wa, wb = edge(a[i], b[i]), edge(b[i], c[i]), edge(c[i], a[i])
        z = wa * depth[i, 0] + wb * depth[i, 1] + wc * depth[i, 2]
        nearest = depth_image[y0:y1, x0:x1]
        win = (wa >= -1e-7) & (wb >= -1e-7) & (wc >= -1e-7) & (z > nearest)
        nearest[win] = z[win]
        index_image[y0:y1, x0:x1][win] = i

    # Triangles are grouped by the size of their block (powers of two in each direction), so that a
    # long thin one costs its own few pixels and not a square of its length. Large ones go one by
    # one afterwards (where two triangles are equally near, the one drawn first stays).
    large = usable & (extent[:, 0] * extent[:, 1] >= LARGE_TRIANGLE_PX)
    usable = usable & ~large
    bucket = np.ceil(np.log2(np.maximum(extent, 1))).astype(np.int64)
    key = np.where(usable, bucket[:, 0] * 32 + bucket[:, 1], -1)
    for value in np.unique(key[key >= 0]):
        width, height = min(size, 2 ** (value // 32)), min(size, 2 ** (value % 32))
        batch = np.nonzero(key == value)[0]
        chunk = max(1, 2_000_000 // (width * height))
        for start in range(0, len(batch), chunk):
            draw(batch[start:start + chunk], width, height)
    index_image, depth_image = index_buffer.reshape(size, size), depth_buffer.reshape(size, size)
    for i in np.nonzero(large)[0]:
        draw_one(i)
    return index_image, depth_image


@dataclass
class Drawing:
    name: str
    mask: np.ndarray     # bool
    depth: np.ndarray    # mm towards the camera from the centre of the box, -inf outside the part
    face: np.ndarray     # index of the visible B-rep face, -1 outside the part
    normal: np.ndarray   # (size, size, 3) of the visible triangle, turned towards the camera
    edges: np.ndarray    # bool: every boundary between two faces, and the outline
    sharp: np.ndarray    # bool: the edges a render would certainly show: creases, occlusions, the outline


def draw(mesh: Mesh, name: str, dims: dict[str, float], mm_per_px: float, size: int = SIZE) -> Drawing:
    right, up, towards = camera(name)
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    relative = mesh.triangles - centre
    half = size / 2
    points = np.stack([relative @ right / mm_per_px + half - 0.5, half - 0.5 - relative @ up / mm_per_px], axis=2)
    index, depth = rasterise(points, relative @ towards, size)
    mask = index >= 0
    face = np.where(mask, mesh.face[index], -1)
    normal = mesh.normal[index] * mask[:, :, None]
    normal *= np.where(normal @ towards < 0, -1.0, 1.0)[:, :, None]

    edges = np.zeros((size, size), bool)
    sharp = np.zeros((size, size), bool)
    cos_crease = np.cos(np.radians(CREASE_DEG))
    for axis in (0, 1):
        first = (slice(None, -1), slice(None)) if axis == 0 else (slice(None), slice(None, -1))
        second = (slice(1, None), slice(None)) if axis == 0 else (slice(None), slice(1, None))
        differ = face[first] != face[second]
        outline = mask[first] != mask[second]
        with np.errstate(invalid="ignore"):
            jump = np.abs(depth[first] - depth[second]) > DEPTH_JUMP_PX * mm_per_px
        crease = (normal[first] * normal[second]).sum(axis=2) < cos_crease
        hard = outline | (differ & mask[first] & mask[second] & (crease | jump))
        # mark the pixel on the part's side (or the nearer one), where a render draws the line
        nearer_first = np.where(outline, mask[first], depth[first] >= depth[second])
        for target, flags in ((edges, differ), (sharp, hard)):
            target[first] |= flags & nearer_first
            target[second] |= flags & ~nearer_first
    return Drawing(name, mask, depth, face, normal, edges, sharp)


# -- comparing ---------------------------------------------------------------------------------------

@dataclass
class ViewCheck:
    silhouette: float   # overlap (IoU) of the two masks
    precision: float    # share of the candidate's sharp edges that lie on a line of the view
    recall: float       # share of the view's lines that lie on a face boundary of the candidate

    @property
    def lines(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0


@dataclass
class Report:
    views: dict[str, ViewCheck] = field(default_factory=dict)
    volume: float | None = None   # candidate / facts.json
    area: float | None = None     # candidate / facts.json

    def mean(self, what: str) -> float:
        values = [getattr(v, what) for v in self.views.values()]
        return float(np.mean(values)) if values else 0.0

    @property
    def score(self) -> float:
        """0..1, higher is better: the lines and silhouettes agree and the volume is right."""
        if not self.views:
            return 0.0
        ratio = 1.0 if not self.volume else min(self.volume, 1 / self.volume)
        return self.mean("lines") * self.mean("silhouette") * ratio

    def as_dict(self) -> dict:
        return {"score": round(self.score, 4), "volume": self.volume and round(self.volume, 4),
                "area": self.area and round(self.area, 4), "silhouette": round(self.mean("silhouette"), 4),
                "precision": round(self.mean("precision"), 4), "recall": round(self.mean("recall"), 4),
                "views": {name: {"silhouette": round(v.silhouette, 4), "precision": round(v.precision, 4),
                                 "recall": round(v.recall, 4)} for name, v in self.views.items()}}


def _near(pixels: np.ndarray, target: np.ndarray) -> float:
    """Share of the set pixels of `pixels` that have a set pixel of `target` within the tolerance."""
    count = int(pixels.sum())
    if count == 0:
        return 1.0
    if not target.any():
        return 0.0
    distance = cv2.distanceTransform((~target).astype(np.uint8), cv2.DIST_L2, 3)
    return float((distance[pixels] <= TOLERANCE_PX).sum() / count)


def compare(observed: Observed, drawing: Drawing) -> ViewCheck:
    union = (observed.mask | drawing.mask).sum()
    silhouette = float((observed.mask & drawing.mask).sum() / union) if union else 0.0
    return ViewCheck(silhouette, _near(drawing.sharp, observed.lines), _near(observed.lines, drawing.edges))


def measure(shape: TopoDS_Shape) -> tuple[float, float]:
    """Volume (mm3) and surface area (mm2)."""
    volume, area = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, volume)
    BRepGProp.SurfaceProperties_s(shape, area)
    return volume.Mass(), area.Mass()


def check(shape: TopoDS_Shape, case_dir: Path, observed: dict[str, Observed] | None = None,
          views: tuple[str, ...] | None = None) -> Report:
    """Compare a candidate solid with the views and facts of a case."""
    facts = json.loads((case_dir / "facts.json").read_text())
    dims = {k: float(facts["bounding_box_mm"][k]) for k in "xyz"}
    if observed is None:
        observed = observe(case_dir, dims)
    report = Report()
    if observed:
        mesh = tessellate(shape, 0.3 * min(o.mm_per_px for o in observed.values()))
        for name, view in observed.items():
            if views is None or name in views:
                report.views[name] = compare(view, draw(mesh, name, dims, view.mm_per_px, view.mask.shape[0]))
    volume, area = measure(shape)
    if facts.get("volume_mm3"):
        report.volume = volume / facts["volume_mm3"]
    if facts.get("surface_area_mm2"):
        report.area = area / facts["surface_area_mm2"]
    return report
