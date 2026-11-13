"""What the user marked on a solid in the dashboard, worked out so that the place is unmistakable.

In the dashboard: M, click the solid (ours or the ground truth; a click on a mark removes it), a
note if wanted, Send. That writes `runs/_marks/<id>/marks.json` with a picture of the view as it was
(`shot_ours.png`, `shot_truth.png`). This tool reads it and says, for every mark:

- the point, in mm from the min corner of the part's box (the frame of `facts.json` and our solids);
- the face of the solid it lies on: its kind and its numbers (the plane's level, a cylinder's axis
  and radius, ...), its area and its box;
- along the normal: how much material our solid has under the point and how much room over it, and,
  where the case has a ground truth, whether the point is inside the true part and how far the
  true surface is (so: how much is too much, or missing, exactly there);
- in each of the 8 views: the pixel the point falls on, whether the marked solid shows it to that
  camera, and what the picture has there;
- `mark_<n>.png`: the 8 views with the place ringed, each whole and enlarged around it.

    uv run python tools/marks.py            # the marks sent last
    uv run python tools/marks.py 20261112-213045
    uv run python tools/marks.py --list

A debugging tool: it reads ground truth and is no part of the agent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("TRACE", "0")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from OCP.Bnd import Bnd_Box  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.BRepBndLib import BRepBndLib  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex  # noqa: E402
from OCP.BRepClass3d import BRepClass3d_SolidClassifier  # noqa: E402
from OCP.BRepExtrema import BRepExtrema_DistShapeShape  # noqa: E402
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus  # noqa: E402
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector  # noqa: E402
from OCP.STEPControl import STEPControl_Reader  # noqa: E402
from OCP.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_ON, TopAbs_REVERSED, TopAbs_SHELL  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402

from agentic_cad import verify  # noqa: E402

MARKS = Path(os.environ.get("TRACE_DIR") or ROOT / "runs").resolve() / "_marks"
EPS = 1e-3     # mm: closer than this along a line is the marked surface itself
ON = 0.05      # mm: a surface this near the point is "here"
CROP = 120     # px of a view around the place, shown enlarged
TILE = 360

_solids: dict[str, dict] = {}


def solid(path: str) -> dict:
    """A .stp read once: the shape, its faces, the exact min corner of its box."""
    if path not in _solids:
        reader = STEPControl_Reader()
        reader.ReadFile(str(ROOT / path))
        reader.TransferRoots()
        shape = reader.OneShape()
        faces, explorer = [], TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            faces.append(TopoDS.Face(explorer.Current()))
            explorer.Next()
        _solids[path] = {"shape": shape, "faces": faces, "min": np.array(box_of(shape)[0])}
    return _solids[path]


def box_of(shape) -> tuple[list[float], list[float]]:
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    low, high = box.CornerMin(), box.CornerMax()
    return [low.X(), low.Y(), low.Z()], [high.X(), high.Y(), high.Z()]


def vertex(p):
    return BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, p))).Vertex()


def nearest(p, shape) -> tuple[float, np.ndarray]:
    """How far the shape is from p, and its point nearest to p."""
    found = BRepExtrema_DistShapeShape(vertex(p), shape)
    if not found.IsDone() or found.NbSolution() < 1:
        return float("nan"), np.asarray(p, float)
    q = found.PointOnShape2(1)
    return found.Value(), np.array([q.X(), q.Y(), q.Z()])


def distance(p, shape) -> float:
    """To the surface of a solid. Measured to its shells: to the solid itself a point inside is at 0."""
    shells, explorer = [], TopExp_Explorer(shape, TopAbs_SHELL)
    while explorer.More():
        shells.append(explorer.Current())
        explorer.Next()
    return min(nearest(p, skin)[0] for skin in shells or [shape])


def inside(p, shape) -> str:
    state = BRepClass3d_SolidClassifier(shape, gp_Pnt(*map(float, p)), 1e-6).State()
    return "in" if state == TopAbs_IN else "on" if state == TopAbs_ON else "out"


def crossings(p, direction, shape) -> np.ndarray:
    """Where the line through p meets the shape's surface: signed mm along `direction`, sorted."""
    cutter = IntCurvesFace_ShapeIntersector()
    cutter.Load(shape, 1e-6)
    cutter.Perform(gp_Lin(gp_Pnt(*map(float, p)), gp_Dir(*map(float, direction))), -1e9, 1e9)
    return np.array(sorted(cutter.WParameter(i) for i in range(1, cutter.NbPnt() + 1)))


def nearest_face(p, faces) -> tuple[int, float, np.ndarray]:
    """The face a clicked point lies on, how far off it is, and the point of the face itself: a click
    lands on the dashboard's triangles, a hair inside a convex face."""
    near = []
    for i, face in enumerate(faces):  # only faces whose box holds the point are measured exactly
        low, high = box_of(face)
        if all(low[k] - 0.5 <= p[k] <= high[k] + 0.5 for k in range(3)):
            near.append(i)
    best = (float("inf"), -1, np.asarray(p, float))
    for i in near or range(len(faces)):
        gap, q = nearest(p, faces[i])
        if gap < best[0]:
            best = (gap, i, q)
    return best[1], best[0], best[2]


def fmt(values, digits=2) -> str:
    return "(" + ", ".join(f"{v:.{digits}f}" for v in values) + ")"


def axis_name(d) -> str | None:
    d = np.asarray(d, float)
    k = int(np.argmax(np.abs(d)))
    return ("+" if d[k] > 0 else "-") + "XYZ"[k] if abs(d[k]) > 0.9995 else None


def direction(d) -> str:
    return axis_name(d) or fmt(d)


def describe(face, origin) -> str:
    """The surface under a face, with its numbers in the part's frame."""
    surface = BRepAdaptor_Surface(face)
    kind = surface.GetType()
    flipped = face.Orientation() == TopAbs_REVERSED
    xyz = lambda p: np.array([p.X(), p.Y(), p.Z()]) - origin  # noqa: E731
    vec = lambda d: np.array([d.X(), d.Y(), d.Z()])  # noqa: E731
    if kind == GeomAbs_Plane:
        plane = surface.Plane()
        normal = vec(plane.Axis().Direction()) * (-1 if flipped else 1)
        name = axis_name(normal)
        at = xyz(plane.Location())
        what = f"plane {'xyz'[np.argmax(np.abs(normal))]} = {at[np.argmax(np.abs(normal))]:.2f} mm, facing {name}" if name \
            else f"slanted plane, normal {fmt(normal)}, through {fmt(at)}"
    elif kind == GeomAbs_Cylinder:
        cylinder = surface.Cylinder()
        axis = vec(cylinder.Axis().Direction())
        what = (f"cylinder r {cylinder.Radius():.2f} mm ({'hole or concave' if flipped else 'boss or convex'}), "
                f"axis {direction(axis).lstrip('+-') if axis_name(axis) else fmt(axis)} through {fmt(xyz(cylinder.Location()))}")
    elif kind == GeomAbs_Cone:
        cone = surface.Cone()
        what = (f"cone half angle {abs(np.degrees(cone.SemiAngle())):.1f} deg, axis {direction(vec(cone.Axis().Direction()))}, "
                f"apex {fmt(xyz(cone.Apex()))}")
    elif kind == GeomAbs_Torus:
        torus = surface.Torus()
        what = (f"torus R {torus.MajorRadius():.2f} r {torus.MinorRadius():.2f} mm, axis {direction(vec(torus.Axis().Direction()))}, "
                f"centre {fmt(xyz(torus.Location()))}")
    elif kind == GeomAbs_Sphere:
        sphere = surface.Sphere()
        what = f"sphere r {sphere.Radius():.2f} mm, centre {fmt(xyz(sphere.Location()))}"
    else:
        what = "free-form surface (spline, extrusion or revolution)"
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    low, high = box_of(face)
    return f"{what}; area {props.Mass():.1f} mm2; box {fmt(np.array(low) - origin, 1)} to {fmt(np.array(high) - origin, 1)}"


def along_normal(p, normal, shape, label: str) -> str:
    """Material under the point and room over it, along the mark's normal, in one solid."""
    w = crossings(p, normal, shape)
    below, above = w[w < -EPS], w[w > EPS]
    under = inside(np.asarray(p) - 10 * EPS * np.asarray(normal), shape) == "in"
    parts = []
    if under and len(below):
        parts.append(f"{label} is solid for {-below[-1]:.2f} mm under the point")
    elif len(below):
        parts.append(f"{label} is empty under the point for {-below[-1]:.2f} mm")
    else:
        parts.append(f"{label} has nothing under the point")
    parts.append(f"over it free for {above[0]:.2f} mm, then {label} again" if len(above) and not
                 inside(np.asarray(p) + 10 * EPS * np.asarray(normal), shape) == "in" else
                 f"over it {label} goes on for {above[0]:.2f} mm" if len(above) else "over it open")
    return "; ".join(parts)


def against(p, normal, other, name: str) -> str:
    """The point of one solid seen from the other: inside or outside, and how far its surface is."""
    state, gap = inside(p, other), distance(p, other)
    if gap <= ON:
        return f"{name} has its surface here too ({gap:.3f} mm away)"
    w = crossings(p, normal, other)
    below, above = w[w < -EPS], w[w > EPS]
    line = (f"along the normal its surface is {-below[-1]:.2f} mm under the point" if len(below) else "along the normal nothing of it under the point") \
        + (f" and {above[0]:.2f} mm over it" if len(above) else "")
    return f"the point is {'INSIDE' if state == 'in' else 'OUTSIDE'} {name}, {gap:.2f} mm from its nearest surface; {line}"


def in_views(point, model_point, shape, observed, dims) -> list[dict]:
    rows, centre = [], np.array([dims["x"], dims["y"], dims["z"]]) / 2
    for name, view in observed.items():
        right, up, towards = verify.camera(name)
        size = view.image.shape[0]
        relative = np.asarray(point) - centre
        x = relative @ right / view.mm_per_px + size / 2 - 0.5
        y = size / 2 - 0.5 - relative @ up / view.mm_per_px
        cutter = IntCurvesFace_ShapeIntersector()
        cutter.Load(shape, 1e-6)
        cutter.Perform(gp_Lin(gp_Pnt(*map(float, model_point)), gp_Dir(*map(float, towards))), 10 * EPS, 1e9)
        row = {"view": name, "x": float(x), "y": float(y), "seen": cutter.NbPnt() == 0}
        xi, yi = int(round(x)), int(round(y))
        if 0 <= xi < size and 0 <= yi < size:
            row["part"] = bool(view.mask[yi, xi])
            row["tone"] = int(view.image[yi, xi].max())
            row["line"] = bool(view.lines[max(yi - 2, 0):yi + 3, max(xi - 2, 0):xi + 3].any())
        rows.append(row)
    return rows


def sheet(rows: list[dict], observed, title: str, path: Path) -> None:
    """The 8 views with the place ringed (red where the marked solid shows it to that camera, grey
    where it is hidden): each view whole, and under it the surroundings enlarged."""
    tiles = []
    for row in rows:
        image = observed[row["view"]].image
        size = image.shape[0]
        colour = (40, 40, 235) if row["seen"] else (150, 150, 150)
        whole = cv2.resize(image, (TILE, TILE), interpolation=cv2.INTER_AREA)
        k = TILE / size
        cv2.circle(whole, (int(round(row["x"] * k)), int(round(row["y"] * k))), 9, colour, 2, cv2.LINE_AA)
        padded = cv2.copyMakeBorder(image, CROP, CROP, CROP, CROP, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        xi, yi = int(round(row["x"])) + CROP, int(round(row["y"])) + CROP
        xi, yi = min(max(xi, CROP // 2), size + CROP + CROP // 2), min(max(yi, CROP // 2), size + CROP + CROP // 2)
        crop = padded[yi - CROP // 2:yi + CROP // 2, xi - CROP // 2:xi + CROP // 2]
        near = cv2.resize(crop, (TILE, TILE), interpolation=cv2.INTER_NEAREST)
        cv2.circle(near, (TILE // 2, TILE // 2), 14, colour, 2, cv2.LINE_AA)
        cv2.circle(near, (TILE // 2, TILE // 2), 1, colour, -1)
        label = f"{row['view']} ({row['x']:.0f}, {row['y']:.0f})" + ("" if row["seen"] else " hidden")
        for tile, text in ((whole, label), (near, f"x{TILE // CROP}")):
            cv2.putText(tile, text, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.putText(tile, text, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
        tiles.append(np.vstack([whole, near]))
    if not tiles:
        return
    while len(tiles) % 4:
        tiles.append(np.full_like(tiles[0], 255))
    grid = np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)])
    cv2.putText(grid, title, (8, grid.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), grid)


def report(folder: Path, pictures: bool = True) -> str:
    record = json.loads((folder / "marks.json").read_text())
    case_dir = ROOT / record["inputs"]
    out = [f"# Marks {record['id']}: {record['dataset']} {record['case']}"
           + (f", run {record['run']}" if record.get("run") else ", committed output") + f" (sent {record['saved_at']})",
           "Points are in mm from the min corner of the part's box (the frame of facts.json and of our solids).",
           *(f"View as the user had it: {folder.relative_to(ROOT) / shot}" for shot in record.get("shots", []))]
    observed, dims = {}, None
    try:
        box = json.loads((case_dir / "facts.json").read_text())["bounding_box_mm"]
        dims = {k: float(box[k]) for k in "xyz"}
        observed = verify.observe(case_dir, dims)
        out.append(f"Box {dims['x']:g} x {dims['y']:g} x {dims['z']:g} mm; inputs {record['inputs']}")
    except Exception as error:  # the marks are still worth reading without the views
        out.append(f"(views not read: {type(error).__name__}: {error})")
    truth = solid(record["truth_file"]) if record.get("truth_file") else None
    for mark in record["marks"]:
        point, normal = np.array(mark["point"], float), np.array(mark["normal"], float)
        on_truth = mark["on"] == "truth"
        step = mark.get("step") or {}
        where = "ground truth" if on_truth else "our solid" + (f", step {step.get('n')} {step.get('name')} ({step.get('trace')})" if step else "")
        out += ["", f"## Mark {mark['n']} on {where}" + (f': "{mark["note"]}"' if mark.get("note") else ""),
                f"- point {fmt(point)} mm, normal {direction(normal)}"
                + (f"; INSIDE the material, on the section square to {mark.get('section')}" if mark["kind"] == "section" else "")
                + ("; the view was showing the difference to the ground truth" if mark.get("diff") else "")]
        if dims:
            share = point / np.array([dims["x"], dims["y"], dims["z"]])
            out.append(f"- at {share[0]:.0%} of x, {share[1]:.0%} of y, {share[2]:.0%} of z of the box")
        if not mark.get("solid_file"):
            out.append("- (its .stp is not on disk any more)")
            continue
        out.append(f"- file {mark['solid_file']}")
        try:
            own = solid(mark["solid_file"])
            origin = own["min"] if on_truth else np.zeros(3)   # the ground truth lies anywhere
            model_point = point + origin
            if mark["kind"] == "surface":
                index, gap, snapped = nearest_face(model_point, own["faces"])
                if index >= 0:
                    if gap <= 0.5:
                        model_point, point = snapped, snapped - origin
                    out.append(f"- face {index} of {len(own['faces'])}"
                               + (f" ({gap:.3f} mm from the click)" if gap > ON else "") + f": {describe(own['faces'][index], origin)}")
            label = "the true part" if on_truth else "our solid"
            out.append(f"- along the normal: {along_normal(model_point, normal, own['shape'], label)}")
            if on_truth:
                final = record.get("marks") and next((m for m in record["marks"] if m["on"] == "ours" and m.get("solid_file")), None)
                if final:
                    other = solid(final["solid_file"])
                    out.append(f"- against our solid ({final['solid_file']}): {against(point, normal, other['shape'], 'our solid')}")
            elif truth:
                out.append(f"- against the ground truth: {against(point + truth['min'], normal, truth['shape'], 'the true part')}")
            if observed and dims:
                rows = in_views(point, model_point, own["shape"], observed, dims)
                for row in rows:
                    there = "outside the picture" if "part" not in row else \
                        f"picture has {'part' if row['part'] else 'background'}, tone {row['tone']}" + (", a line within 2 px" if row["line"] else "")
                    out.append(f"  - {row['view']}: pixel ({row['x']:.1f}, {row['y']:.1f}), "
                               f"{'seen' if row['seen'] else 'hidden'} in this solid; {there}")
                if pictures:
                    path = folder / f"mark_{mark['n']}.png"
                    sheet(rows, observed, f"mark {mark['n']} {fmt(point)} mm, {record['case']}", path)
                    out.append(f"- views with the place ringed: {path.relative_to(ROOT)}")
        except Exception as error:
            out.append(f"- (not worked out: {type(error).__name__}: {error})")
    text = "\n".join(out) + "\n"
    (folder / "report.md").write_text(text)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("id", nargs="?", help="which marks; default: those sent last")
    parser.add_argument("--list", action="store_true", help="list what has been sent")
    parser.add_argument("--no-pictures", action="store_true")
    args = parser.parse_args()
    if args.list:
        for folder in sorted(MARKS.glob("*/marks.json")):
            record = json.loads(folder.read_text())
            print(record["id"], record["dataset"], record["case"], f"{len(record['marks'])} marks")
        return
    name = args.id or ((MARKS / "latest").read_text().strip() if (MARKS / "latest").is_file() else "")
    folder = MARKS / name
    if not name or not (folder / "marks.json").is_file():
        sys.exit(f"no marks{' ' + name if name else ''} under {MARKS}: mark places in the dashboard (M) and press Send")
    print(report(folder, not args.no_pictures), end="")


if __name__ == "__main__":
    main()
