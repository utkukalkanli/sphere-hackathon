"""Step 1 of the pipeline, fully deterministic: carve the part's bounding box with its silhouettes.

1. Make a box with the bounding-box dimensions from facts.json.
2. Put the six orthographic views on the faces of that box. Every view shows the part's silhouette
   filling the box face it looks at, so the silhouette's own bounding rectangle is that face.
3. Whatever is white (background) in a view is cut all the way through the box along the view
   direction.
4. The two isometric views look along the same diagonal from opposite ends, so together they are
   one more direction to cut along. Their scale is not given by a box face; see `iso_silhouette`.

The result is the visual hull: the largest solid that matches every silhouette.

Frame of the views (README): front looks along +Y (X right, Z up), back along -Y (X left, Z up),
left along +X (Y left, Z up), right along -X (Y right, Z up), top along -Z (X right, Y up),
bottom along +Z (X right, Y down), iso_front_right_top from (+X, -Y, +Z) with Z up. The solid is
built with its bounding-box min corner at the origin.
"""

from __future__ import annotations

import json
import multiprocessing
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakePrism
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Pnt, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape

BACKGROUND_MIN = 215   # a pixel is background when its darkest channel is above this
SIMPLIFY_PX = 0.6      # Douglas-Peucker tolerance for the silhouette outlines, in pixels
MIN_AREA_PX = 12.0     # outlines smaller than this are rendering noise
FRAME_MARGIN = 1.04    # the renders leave 4 % around the framed extent (half the image = extent * 1.04)
ISO_CONTAINED = 0.998  # share of the iso silhouette that must fall inside the projected hull

# name -> (main view, opposite view, axis to flip the opposite one onto the main one),
# the box axes of the silhouette's horizontal (u) and vertical (v) direction, and the axis it is cut along
ORTHO = {
    "front_back": {"views": ("front", "back"), "flip": 1, "u": "x", "v": "z", "along": "y"},
    "right_left": {"views": ("right", "left"), "flip": 1, "u": "y", "v": "z", "along": "x"},
    "top_bottom": {"views": ("top", "bottom"), "flip": 0, "u": "x", "v": "y", "along": "z"},
}
UNIT = {"x": np.array([1.0, 0, 0]), "y": np.array([0, 1.0, 0]), "z": np.array([0, 0, 1.0])}
# iso_front_right_top: the camera sits at (+X, -Y, +Z) with Z up
ISO_RIGHT = np.array([1.0, 1.0, 0.0]) / np.sqrt(2)
ISO_UP = np.array([-1.0, 1.0, 2.0]) / np.sqrt(6)
ISO_TOWARDS_CAMERA = np.array([1.0, -1.0, 1.0]) / np.sqrt(3)


@dataclass
class Silhouette:
    name: str                 # front_back, right_left, top_bottom, iso
    views: list[str]          # names of the views it was made from
    mask: np.ndarray          # uint8, 255 = part
    outlines: list[dict]      # [{"outer": Nx2 float (a, b) in mm, "holes": [Nx2, ...]}]
    origin: np.ndarray        # 3D point of (a, b) = (0, 0), in front of the box
    right: np.ndarray         # 3D direction of a
    up: np.ndarray            # 3D direction of b
    direction: np.ndarray     # unit vector the silhouette is cut along
    depth: float              # how far it is extruded: through the whole box
    info: dict = field(default_factory=dict)


def read_dimensions(case_dir: Path | None, prompt: str = "") -> tuple[float, float, float]:
    """Bounding box (x, y, z) in mm: from facts.json, else from the 'Envelope' line of the brief."""
    if case_dir is not None and (case_dir / "facts.json").is_file():
        box = json.loads((case_dir / "facts.json").read_text()).get("bounding_box_mm") or {}
        if all(box.get(k, 0) > 0 for k in "xyz"):
            return float(box["x"]), float(box["y"]), float(box["z"])
    number = r"(\d+(?:\.\d+)?)"
    match = re.search(rf"Envelope[^\d]*{number}\s*[x×]\s*{number}\s*[x×]\s*{number}", prompt)
    if match:
        return float(match.group(1)), float(match.group(2)), float(match.group(3))
    raise ValueError("no bounding box: facts.json is missing and the brief has no Envelope line")


def read_volume(case_dir: Path | None) -> float | None:
    """The part's true volume in mm3 from facts.json, if there: no correct cut can go below it."""
    try:
        value = json.loads((case_dir / "facts.json").read_text()).get("volume_mm3")
        return float(value) if value and value > 0 else None
    except Exception:
        return None


def part_mask(image_path: Path) -> np.ndarray | None:
    """Boolean mask of the part in a view, cropped to the part's own bounding rectangle."""
    image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if image.ndim == 2:
        image = image[:, :, None]
    part = image[:, :, :3].min(axis=2) <= BACKGROUND_MIN
    if image.shape[2] == 4:
        part &= image[:, :, 3] > 127
    rows, cols = np.where(part.any(axis=1))[0], np.where(part.any(axis=0))[0]
    if len(rows) < 2 or len(cols) < 2:
        return None
    return part[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def find_view(views: list[Path], name: str) -> Path | None:
    return next((v for v in views if re.fullmatch(rf"view_\d+_{name}", v.stem)), None)


def trace_outlines(mask: np.ndarray, to_mm) -> list[dict]:
    """Outlines of the part in a mask (255 = part), each with its holes; `to_mm` maps Nx2 pixels to mm."""
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        return []

    def simplify(contour) -> np.ndarray | None:
        if cv2.contourArea(contour) < MIN_AREA_PX:
            return None
        points = cv2.approxPolyDP(contour, SIMPLIFY_PX, True).reshape(-1, 2).astype(float)
        return to_mm(points) if len(points) >= 3 else None

    outlines = []
    for index, contour in enumerate(contours):
        if hierarchy[0][index][3] != -1:  # a hole: handled with its parent
            continue
        outer = simplify(contour)
        if outer is None:
            continue
        holes = []
        child = hierarchy[0][index][2]
        while child != -1:
            hole = simplify(contours[child])
            if hole is not None:
                holes.append(hole)
            child = hierarchy[0][child][0]
        outlines.append({"outer": outer, "holes": holes})
    return outlines


def ortho_silhouette(name: str, views: list[Path], dims: dict[str, float]) -> Silhouette | None:
    spec = ORTHO[name]
    masks, used = [], []
    for index, view in enumerate(spec["views"]):
        path = find_view(views, view)
        mask = part_mask(path) if path else None
        if mask is None:
            continue
        if index == 1:  # the opposite view sees the same silhouette mirrored
            mask = np.flip(mask, axis=spec["flip"])
        masks.append(mask)
        used.append(view)
    if not masks:
        return None
    # Two opposite views must agree that something is background before it is cut away: a bright
    # highlight in one render then cannot punch a hole through the part.
    height, width = masks[0].shape
    part = np.zeros((height, width), dtype=bool)
    for mask in masks:
        if mask.shape != (height, width):
            mask = cv2.resize(mask.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST) > 0
        part |= mask
    mask = part.astype(np.uint8) * 255

    du, dv = dims[spec["u"]], dims[spec["v"]]
    outlines = trace_outlines(mask, lambda p: np.column_stack(
        [p[:, 0] / (width - 1) * du, (height - 1 - p[:, 1]) / (height - 1) * dv]))
    if not outlines:
        return None
    margin = 0.05 * max(dims.values())
    along = UNIT[spec["along"]]
    return Silhouette(name=name, views=used, mask=mask, outlines=outlines, origin=-margin * along,
                      right=UNIT[spec["u"]], up=UNIT[spec["v"]], direction=along,
                      depth=dims[spec["along"]] + 2 * margin)


def full_mask(image_path: Path) -> np.ndarray | None:
    """Boolean mask of the part in a view, not cropped: the position in the frame matters."""
    image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if image.ndim == 2:
        image = image[:, :, None]
    part = image[:, :, :3].min(axis=2) <= BACKGROUND_MIN
    if image.shape[2] == 4:
        part &= image[:, :, 3] > 127
    return part if part.any() else None


def triangles(shape: TopoDS_Shape, deflection: float) -> np.ndarray:
    """Tessellate a shape: array of triangles, shape (n, 3, 3)."""
    BRepMesh_IncrementalMesh(shape, deflection)
    result = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(TopoDS.Face(explorer.Current()), location)
        if mesh is not None:
            transform = location.Transformation()
            nodes = []
            for i in range(1, mesh.NbNodes() + 1):
                point = mesh.Node(i).Transformed(transform)
                nodes.append((point.X(), point.Y(), point.Z()))
            for i in range(1, mesh.NbTriangles() + 1):
                a, b, c = mesh.Triangle(i).Get()
                result.append((nodes[a - 1], nodes[b - 1], nodes[c - 1]))
        explorer.Next()
    return np.array(result, dtype=float).reshape(-1, 3, 3)


def iso_silhouette(views: list[Path], dims: dict[str, float], hull: TopoDS_Shape) -> Silhouette | None:
    """The silhouette seen along the box diagonal, from the two isometric views.

    The part is drawn around the image centre (the projected centre of the bounding box), but how many
    mm a pixel is depends on what the renderer framed. Two conventions are tried, and one is accepted
    only if the silhouette then falls inside the projection of `hull` (the solid carved so far), which
    the true silhouette always does:
      1. the frame fits the projected bounding box: exact, from the dimensions alone;
      2. the frame fits the part itself: its extent is taken from `hull`.
    """
    masks, used = [], []
    for view, mirrored in (("iso_front_right_top", False), ("iso_back_left_bottom", True)):
        path = find_view(views, view)
        mask = full_mask(path) if path else None
        if mask is None:
            continue
        masks.append(np.flip(mask, axis=1) if mirrored else mask)  # seen from the other end: mirrored
        used.append(view)
    if not masks or any(m.shape != masks[0].shape or m.shape[0] != m.shape[1] for m in masks):
        return None
    part = masks[0]
    for mask in masks[1:]:
        part = part | mask
    size = part.shape[0]
    half_px = size / 2
    mask = part.astype(np.uint8) * 255

    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    corners = np.array([[x, y, z] for x in (0, dims["x"]) for y in (0, dims["y"]) for z in (0, dims["z"])]) - centre
    tris = triangles(hull, 0.01 * float(np.linalg.norm(centre))) - centre
    if not len(tris):
        return None
    hull_a, hull_b = tris @ ISO_RIGHT, tris @ ISO_UP  # (n, 3) each

    def half_extent(points: np.ndarray) -> float:
        return float(max(np.abs(points @ ISO_RIGHT).max(), np.abs(points @ ISO_UP).max()))

    def contained(mm_per_px: float) -> float:
        cols = hull_a / mm_per_px + half_px - 0.5
        rows = half_px - 0.5 - hull_b / mm_per_px
        polygons = np.stack([cols, rows], axis=2).round().astype(np.int32)
        projected = np.zeros((size, size), np.uint8)
        for triangle in polygons:  # one by one: fillPoly's even-odd rule would cancel overlapping ones
            cv2.fillConvexPoly(projected, triangle, 255)
        projected = cv2.dilate(projected, np.ones((5, 5), np.uint8))
        return float((part & (projected > 0)).sum() / part.sum())

    candidates = [("frame fits the bounding box", half_extent(corners)),
                  ("frame fits the part", half_extent(tris.reshape(-1, 3)))]
    chosen, tried = None, {}
    for label, half_mm in candidates:
        mm_per_px = half_mm * FRAME_MARGIN / half_px
        tried[label] = round(contained(mm_per_px), 4)
        if tried[label] >= ISO_CONTAINED:
            chosen = (label, mm_per_px)
            break
    if chosen is None:
        return None
    label, mm_per_px = chosen

    outlines = trace_outlines(mask, lambda p: np.column_stack(
        [(p[:, 0] + 0.5 - half_px) * mm_per_px, (half_px - 0.5 - p[:, 1]) * mm_per_px]))
    if not outlines:
        return None
    reach = 2 * float(np.linalg.norm(centre))  # further than any point of the box from its centre
    return Silhouette(name="iso", views=used, mask=mask, outlines=outlines,
                      origin=centre + reach * ISO_TOWARDS_CAMERA, right=ISO_RIGHT, up=ISO_UP,
                      direction=-ISO_TOWARDS_CAMERA, depth=2 * reach,
                      info={"scale": label, "mm_per_px": round(mm_per_px, 5), "inside_hull": tried})


# -- solids ----------------------------------------------------------------------------------------

def _signed_area(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _wire(points: np.ndarray, silhouette: Silhouette, counter_clockwise: bool):
    if (_signed_area(points) > 0) != counter_clockwise:
        points = points[::-1]
    polygon = BRepBuilderAPI_MakePolygon()
    for a, b in points:
        polygon.Add(gp_Pnt(*map(float, silhouette.origin + a * silhouette.right + b * silhouette.up)))
    polygon.Close()
    return polygon.Wire()


def prism(silhouette: Silhouette) -> TopoDS_Shape:
    """The silhouette extruded through the whole box (and beyond) along its view direction."""
    vector = gp_Vec(*map(float, silhouette.direction * silhouette.depth))
    result = None
    for outline in silhouette.outlines:
        face = BRepBuilderAPI_MakeFace(_wire(outline["outer"], silhouette, True), True)
        for hole in outline["holes"]:
            face.Add(_wire(hole, silhouette, False))
        solid = BRepPrimAPI_MakePrism(face.Face(), vector).Shape()
        result = solid if result is None else BRepAlgoAPI_Fuse(result, solid).Shape()
    return result


def volume(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def largest_solid(shape: TopoDS_Shape) -> TopoDS_Shape | None:
    best, best_volume = None, 0.0
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solid = TopoDS.Solid(explorer.Current())
        if (size := volume(solid)) > best_volume:
            best, best_volume = solid, size
        explorer.Next()
    return best


def box(dims: dict[str, float]) -> TopoDS_Shape:
    return BRepPrimAPI_MakeBox(dims["x"], dims["y"], dims["z"]).Shape()


def cut(shape: TopoDS_Shape, silhouette: Silhouette) -> TopoDS_Shape | None:
    """Keep only what lies inside the silhouette: everything white in the view is cut through.
    Returns one valid solid, or None when the boolean operation fails."""
    common = BRepAlgoAPI_Common(shape, prism(silhouette))
    if not common.IsDone():
        return None
    solid = largest_solid(common.Shape())
    if solid is None or volume(solid) <= 0 or not BRepCheck_Analyzer(solid).IsValid():
        return None
    return solid


def read_brep(path: str) -> TopoDS_Shape:
    shape = TopoDS_Shape()
    BRepTools.Read_s(shape, path, BRep_Builder())
    return shape


def _run_in_child(operation, shape_path: str, argument, result_path: str) -> bool:
    result = operation(read_brep(shape_path), argument)
    if result is None:
        return False
    BRepTools.Write_s(result, result_path)
    return True


class Cutter:
    """Runs cuts in a child process, so that a boolean operation that never finishes can be abandoned.

    OpenCascade occasionally spends minutes (and gigabytes) on one cut; nothing inside the process can
    interrupt it, and the whole run has a wall-clock limit.
    """

    def __init__(self):
        self._pool = None

    def run(self, operation, shape: TopoDS_Shape, argument, timeout: float) -> TopoDS_Shape | None:
        """operation(shape, argument) -> solid or None, run in the child. `operation` must be a
        module-level function and `argument` picklable. Raises TimeoutError after `timeout` seconds."""
        if self._pool is None:
            self._pool = multiprocessing.get_context("spawn").Pool(1)
        with tempfile.TemporaryDirectory() as tmp:
            shape_path, result_path = f"{tmp}/in.brep", f"{tmp}/out.brep"
            BRepTools.Write_s(shape, shape_path)
            job = self._pool.apply_async(_run_in_child, (operation, shape_path, argument, result_path))
            try:
                done = job.get(timeout)
            except multiprocessing.TimeoutError:
                self.close()
                raise TimeoutError(f"took longer than {timeout:.0f} s") from None
            return largest_solid(read_brep(result_path)) if done else None

    def cut(self, shape: TopoDS_Shape, silhouette: Silhouette, timeout: float) -> TopoDS_Shape | None:
        """Like `cut`, but raises TimeoutError when it takes longer than `timeout` seconds."""
        return self.run(cut, shape, silhouette, timeout)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.terminate()
            self._pool = None


def tidy(shape: TopoDS_Shape) -> TopoDS_Shape:
    """Merge faces that lie on the same plane; cosmetic, so any failure keeps the input."""
    try:
        unify = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
        unify.Build()
        solid = largest_solid(unify.Shape())
        if solid is not None and BRepCheck_Analyzer(solid).IsValid():
            return solid
    except Exception:
        pass
    return shape


def write_step(shape: TopoDS_Shape, path: Path) -> None:
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"could not write {path}")


def preview(silhouette: Silhouette) -> bytes:
    """PNG of the silhouette: grey is kept, white is cut through. Outlines are drawn back from their
    mm coordinates (red = outer, blue = holes), so a wrong mapping shows as outlines off the grey."""
    height, width = silhouette.mask.shape
    image = np.full((height, width, 3), 255, np.uint8)
    image[silhouette.mask > 0] = (205, 205, 205)
    if silhouette.name == "iso":
        scale = silhouette.info["mm_per_px"]
        to_px = lambda p: np.column_stack([p[:, 0] / scale + width / 2 - 0.5, height / 2 - 0.5 - p[:, 1] / scale])
    else:
        span_a = max(o["outer"][:, 0].max() for o in silhouette.outlines)
        span_b = max(o["outer"][:, 1].max() for o in silhouette.outlines)
        to_px = lambda p: np.column_stack([p[:, 0] / span_a * (width - 1), (height - 1) * (1 - p[:, 1] / span_b)])
    thickness = max(1, max(width, height) // 350)
    for outline in silhouette.outlines:
        for points, colour in [(outline["outer"], (40, 40, 220))] + [(h, (220, 120, 40)) for h in outline["holes"]]:
            cv2.polylines(image, [to_px(points).round().astype(np.int32).reshape(-1, 1, 2)], True, colour, thickness)
    return cv2.imencode(".png", image)[1].tobytes()
