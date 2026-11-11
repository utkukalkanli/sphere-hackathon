"""Process ONE STEP file into the Sphere Forge test-case format.

  python -I part_worker.py scan  <in.step>
  python -I part_worker.py build <in.step> <eval_root> <n> <source> <source_id> [name]

build writes:
  <eval_root>/inputs/test_case_<n>/{description.md, facts.json, images/view_*.png}
  <eval_root>/ground_truth/test_case_<n>.stp

Prints one JSON line on stdout. Meant to run in a subprocess so an OCC crash or
hang on a bad file cannot take the driver down.
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cadquery as cq
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import (
    GeomAbs_BSplineSurface, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus,
)
from OCP.gp import gp_Pnt, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_VERTEX, TopAbs_WIRE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedMapOfShape

VIEW_SIZE = 1024
PART_COLOR = "#c9a063"
EDGE_COLOR = "#1a1a1a"
FACE_TYPE = {
    GeomAbs_Plane: "PLANE", GeomAbs_Cylinder: "CYLINDER", GeomAbs_Cone: "CONE",
    GeomAbs_Torus: "TORUS", GeomAbs_Sphere: "SPHERE", GeomAbs_BSplineSurface: "BSPLINE",
}
# README camera table. name -> (direction from part towards camera, view-up)
VIEWS = {
    "view_0_front": ((0, -1, 0), (0, 0, 1)),
    "view_1_back": ((0, 1, 0), (0, 0, 1)),
    "view_2_left": ((-1, 0, 0), (0, 0, 1)),
    "view_3_right": ((1, 0, 0), (0, 0, 1)),
    "view_4_top": ((0, 0, 1), (0, 1, 0)),
    "view_5_bottom": ((0, 0, -1), (0, -1, 0)),
    "view_6_iso_front_right_top": ((1, -1, 1), (0, 0, 1)),
    "view_7_iso_back_left_bottom": ((-1, 1, -1), (0, 0, 1)),
}


def tier_of(n_faces: int) -> str:
    return "simple" if n_faces <= 30 else "medium" if n_faces <= 200 else "complex"


def _map(shape, kind):
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, m)
    return m


def _bbox(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    return box.Get()


def _axis_label(d):
    for i, n in enumerate("XYZ"):
        if abs(abs(d[i]) - 1) < 1e-4:
            return n
    return "(" + ", ".join(f"{v:.2f}" for v in d) + ")"


def _canon(d):
    for c in d:
        if abs(c) > 1e-6:
            return tuple(-v for v in d) if c < 0 else tuple(d)
    return tuple(d)


def load_single_solid(path: str):
    solids = []
    for obj in cq.importers.importStep(path).vals():
        solids.extend(obj.Solids())
    if len(solids) != 1:
        return None, f"n_solids={len(solids)}"
    solid = solids[0]
    if not BRepCheck_Analyzer(solid.wrapped).IsValid():
        return None, "invalid_brep"
    if solid.Volume() <= 1e-6:
        return None, "non_positive_volume"
    x0, y0, z0, *_ = _bbox(solid.wrapped)
    return solid.translate(cq.Vector(-x0, -y0, -z0)), "ok"  # bbox min corner -> origin


def measure(solid) -> dict:
    """facts.json, same keys as the organisers' files. Solid must have its bbox min at the origin."""
    s = solid.wrapped
    faces, edges = _map(s, TopAbs_FACE), _map(s, TopAbs_EDGE)
    verts, wires = _map(s, TopAbs_VERTEX), _map(s, TopAbs_WIRE)
    n_edges = sum(
        0 if BRep_Tool.Degenerated_s(TopoDS.Edge_s(edges.FindKey(i))) else 1
        for i in range(1, edges.Extent() + 1)
    )
    F, V, L = faces.Extent(), verts.Extent(), wires.Extent()
    n_shells = len(solid.Shells())
    # Euler-Poincare: V - E + F - (L - F) = 2 * (shells - genus)
    genus = n_shells - (V - n_edges + F - (L - F)) / 2.0

    x0, y0, z0, x1, y1, z1 = _bbox(s)
    vp, sp = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(s, vp)
    BRepGProp.SurfaceProperties_s(s, sp)

    face_types = Counter()
    cyl = defaultdict(lambda: {"sweep": 0.0, "faces": 0, "max_face_sweep": 0.0})
    tori, cones = Counter(), Counter()
    for i in range(1, F + 1):
        f = TopoDS.Face_s(faces.FindKey(i))
        ad = BRepAdaptor_Surface(f)
        t = ad.GetType()
        face_types[FACE_TYPE.get(t, "OTHER")] += 1
        if t == GeomAbs_Torus:
            tori[f"{round(ad.Torus().MinorRadius(), 2):g}"] += 1
        elif t == GeomAbs_Cone:
            cones[f"{round(abs(math.degrees(ad.Cone().SemiAngle())), 1)}"] += 1
        elif t == GeomAbs_Cylinder:
            c = ad.Cylinder()
            u0, u1, v0, v1 = ad.FirstUParameter(), ad.LastUParameter(), ad.FirstVParameter(), ad.LastVParameter()
            p, d1u, d1v = gp_Pnt(), gp_Vec(), gp_Vec()
            ad.D1((u0 + u1) / 2, (v0 + v1) / 2, p, d1u, d1v)
            n = d1u.Crossed(d1v)
            if f.Orientation() == TopAbs_REVERSED:
                n.Reverse()
            ax = c.Axis()
            loc, dr = ax.Location(), ax.Direction()
            d = (dr.X(), dr.Y(), dr.Z())
            rel = (p.X() - loc.X(), p.Y() - loc.Y(), p.Z() - loc.Z())
            t_ax = sum(a * b for a, b in zip(rel, d))
            radial = tuple(r - t_ax * a for r, a in zip(rel, d))
            concave = (n.X() * radial[0] + n.Y() * radial[1] + n.Z() * radial[2]) < 0
            # axial extent as two 3D points on the axis (v is mm along the axis; sign follows the frame)
            vd = c.Position().Direction()
            e0 = tuple(loc.Coord()[k] + v0 * vd.Coord()[k] for k in range(3))
            e1 = tuple(loc.Coord()[k] + v1 * vd.Coord()[k] for k in range(3))
            dc = _canon(d)
            ta, tb = sorted(sum(a * b for a, b in zip(e, dc)) for e in (e0, e1))
            lt = sum(a * b for a, b in zip(loc.Coord(), dc))
            foot = tuple(a - lt * b for a, b in zip(loc.Coord(), dc))
            key = (round(c.Radius(), 3), tuple(round(v, 3) for v in dc), tuple(round(v, 2) for v in foot),
                   concave, round(ta, 2), round(tb, 2))
            g = cyl[key]
            g["sweep"] += abs(u1 - u0)
            g["faces"] += 1
            g["max_face_sweep"] = max(g["max_face_sweep"], abs(u1 - u0))

    full = {True: defaultdict(list), False: defaultdict(list)}  # concave -> (diam, axis) -> [(len, centre)]
    arcs, fillets = Counter(), Counter()
    for (r, dc, foot, concave, ta, tb), g in cyl.items():
        if g["sweep"] >= 2 * math.pi * 0.98:
            mid = (ta + tb) / 2
            centre = [round(foot[k] + mid * dc[k], 2) for k in range(3)]
            full[concave][(round(2 * r, 2), _axis_label(dc))].append((round(tb - ta, 2), centre))
        elif g["max_face_sweep"] <= math.radians(91):
            fillets[(round(r, 2), "concave fillet" if concave else "convex round")] += g["faces"]
        else:
            arcs[(round(2 * r, 2), "concave" if concave else "convex")] += g["faces"]

    def cyl_list(groups):
        return [
            {"diameter": d, "axis": ax, "count": len(items),
             "lengths": sorted({ln for ln, _ in items}), "centres": [c for _, c in items]}
            for (d, ax), items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), -kv[0][0]))
        ]

    return {
        "bounding_box_mm": {"x": round(x1 - x0, 2), "y": round(y1 - y0, 2), "z": round(z1 - z0, 2)},
        "volume_mm3": round(vp.Mass(), 1),
        "surface_area_mm2": round(sp.Mass(), 1),
        "solids": 1,
        "through_holes_genus": int(round(genus)) if abs(genus - round(genus)) < 1e-6 else None,
        "faces": F,
        "edges": n_edges,
        "face_types": dict(face_types.most_common()),
        "cylindrical_holes_and_bores": cyl_list(full[True]),
        "cylindrical_shafts_and_bosses": cyl_list(full[False]),
        "partial_cylinders_arcs": [{"diameter": d, "type": t, "faces": n} for (d, t), n in sorted(arcs.items())],
        "fillets_rounds_from_cylinders": [{"radius": r, "type": t, "faces": n} for (r, t), n in sorted(fillets.items())],
        "fillets_from_tori_minor_radii": dict(sorted(tori.items(), key=lambda kv: float(kv[0]))),
        "cones_half_angle_deg": dict(sorted(cones.items(), key=lambda kv: float(kv[0]))),
        "_note": "positions in mm from the bounding-box min corner, in the frame of the 8 views",
        "_n_shells": n_shells,
    }


def render_views(solid, facts, out_dir: Path):
    import numpy as np
    import pyvista as pv

    bb = facts["bounding_box_mm"]
    diag = math.sqrt(bb["x"] ** 2 + bb["y"] ** 2 + bb["z"] ** 2) or 1.0
    verts, tris = solid.tessellate(diag * 3e-4, 0.15)
    pts = np.array([[v.x, v.y, v.z] for v in verts], dtype=float)
    cells = np.hstack([np.full((len(tris), 1), 3), np.array(tris, dtype=np.int64)]).ravel()
    mesh = pv.PolyData(pts, cells)
    # tessellate() does not share vertices between B-rep faces, so the mesh
    # boundary edges are exactly the B-rep face boundaries.
    edges = mesh.extract_feature_edges(
        boundary_edges=True, feature_edges=False, manifold_edges=False, non_manifold_edges=False
    )
    centre = (pts.min(0) + pts.max(0)) / 2
    out_dir.mkdir(parents=True, exist_ok=True)
    pl = pv.Plotter(off_screen=True, window_size=(VIEW_SIZE, VIEW_SIZE))
    pl.set_background("white")
    pl.add_mesh(mesh, color=PART_COLOR, smooth_shading=False, ambient=0.3, diffuse=0.75, specular=0.0)
    if edges.n_points:
        pl.add_mesh(edges, color=EDGE_COLOR, line_width=1.5)
    pl.enable_parallel_projection()
    for name, (direction, up) in VIEWS.items():
        d = np.array(direction, dtype=float)
        d /= np.linalg.norm(d)
        right = np.cross(up, d)
        right /= np.linalg.norm(right)
        true_up = np.cross(d, right)
        rel = pts - centre
        half = max(np.abs(rel @ right).max(), np.abs(rel @ true_up).max())
        pl.camera.focal_point = centre
        pl.camera.position = centre + d * diag * 3
        pl.camera.up = up
        pl.camera.parallel_scale = half * 1.04
        pl.reset_camera_clipping_range()
        pl.render()
        pl.screenshot(str(out_dir / f"{name}.png"))
    pl.close()


def make_description(facts: dict, name: str | None) -> str:
    """Template brief in the organisers' section layout. Measured facts only: no
    function or prose shape description (their briefs are LLM-written on top of facts.json)."""
    bb = facts["bounding_box_mm"]
    g = facts["through_holes_genus"]
    ft = facts["face_types"]
    long_ax = max("xyz", key=lambda k: bb[k])
    fill = facts["volume_mm3"] / (bb["x"] * bb["y"] * bb["z"])
    out = [f"# {name or 'Mechanical part'}", ""]
    out += [f"**Type / function:** Single-solid mechanical part with {facts['faces']} faces "
            f"({', '.join(f'{n} {t.lower()}' for t, n in ft.items())}).", ""]
    out += [f"**Envelope:** {bb['x']:g} x {bb['y']:g} x {bb['z']:g} mm (X x Y x Z); "
            f"volume {facts['volume_mm3'] / 1000:.2f} cm^3; {g} through-passage{'s' if g != 1 else ''}.", ""]
    out += ["## Overall shape",
            f"The longest dimension is along {long_ax.upper()} ({bb[long_ax]:g} mm). The solid fills "
            f"{fill * 100:.0f} % of its bounding box; surface area {facts['surface_area_mm2'] / 100:.1f} cm^2.", ""]
    out.append("## Features")

    def cyl_lines(items, label):
        for h in items[:14]:
            cs = "; ".join(f"X={c[0]:g}, Y={c[1]:g}, Z={c[2]:g}" for c in h["centres"][:8])
            more = " ..." if len(h["centres"]) > 8 else ""
            lens = ", ".join(f"{v:g}" for v in h["lengths"])
            out.append(f"- **{label}:** {h['count']}, diameter {h['diameter']:g} mm, axis along {h['axis']}, "
                       f"length {lens} mm. Centre{'s' if h['count'] > 1 else ''} at {cs}{more}.")

    cyl_lines(facts["cylindrical_holes_and_bores"], "Cylindrical holes / bores")
    cyl_lines(facts["cylindrical_shafts_and_bosses"], "Cylindrical shafts / bosses")
    if facts["partial_cylinders_arcs"]:
        out.append("- **Partial cylindrical surfaces:** " + ", ".join(
            f"{a['faces']}x {a['type']} Ø{a['diameter']:g}" for a in facts["partial_cylinders_arcs"][:14]) + ".")
    if facts["cones_half_angle_deg"]:
        out.append("- **Conical faces (half-angle):** " + ", ".join(
            f"{n}x {a}°" for a, n in facts["cones_half_angle_deg"].items()) + ".")
    if out[-1] == "## Features":
        out.append("- No cylindrical or conical features.")
    out += ["", "## Edges and finish"]
    fl = [f"{f['faces']}x {f['type']} R{f['radius']:g}" for f in facts["fillets_rounds_from_cylinders"][:14]]
    fl += [f"{n}x toroidal blend R{r}" for r, n in list(facts["fillets_from_tori_minor_radii"].items())[:14]]
    out.append("- " + (", ".join(fl) + "." if fl else "No fillets or rounds detected; edges are sharp."))
    return "\n".join(out) + "\n"


def main():
    mode, path = sys.argv[1], sys.argv[2]
    try:
        solid, reason = load_single_solid(path)
        if solid is None:
            print(json.dumps({"ok": False, "reason": reason}))
            return
        facts = measure(solid)
        if mode == "build":
            root, n, source, source_id = Path(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
            name = sys.argv[7] if len(sys.argv) > 7 and sys.argv[7] else None
            case = root / "inputs" / f"test_case_{n}"
            (root / "ground_truth").mkdir(parents=True, exist_ok=True)
            case.mkdir(parents=True, exist_ok=True)
            cq.exporters.export(cq.Workplane(obj=solid), str(root / "ground_truth" / f"test_case_{n}.stp"),
                                exportType="STEP")
            render_views(solid, facts, case / "images")
            (case / "description.md").write_text(make_description(facts, name))
            (case / "facts.json").write_text(json.dumps(facts, indent=1))
            (case / "source.json").write_text(json.dumps(
                {"source": source, "source_id": source_id, "tier": tier_of(facts["faces"]), "name": name}, indent=1))
        print(json.dumps({"ok": True, "tier": tier_of(facts["faces"]), **facts}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": f"{type(e).__name__}: {e}"[:300]}))


if __name__ == "__main__":
    main()
