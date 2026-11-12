"""Runs one generated CadQuery script in its own process and reports on the result.

    python -m agentic_cad.runner <script.py> <out_dir> [--case-dir DIR] [--stem NAME]

Writes <out_dir>/<stem>.stp (one solid, bounding-box min corner at the origin), comparison images
and <out_dir>/<stem>.json with measurements, per-view silhouette IoU and any error. The parent
process only reads that JSON, so a crash, hang or runaway boolean in generated code cannot take
the pipeline down.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

MAX_TRIANGLES = 60_000


def find_result(namespace: dict, shown: list):
    import cadquery as cq

    kinds = (cq.Workplane, cq.Shape)
    for key in ("result", "part", "body", "model", "solid"):
        if isinstance(namespace.get(key), kinds):
            return namespace[key]
    if shown:
        return shown[-1]
    candidates = [v for k, v in namespace.items() if not k.startswith("_") and isinstance(v, kinds)]
    if candidates:
        return candidates[-1]
    raise ValueError("the script must assign the final part to a variable named `result`")


def to_single_solid(obj, notes: list):
    """Reduce whatever the script produced to one valid solid."""
    import cadquery as cq

    shapes = [o for o in obj.vals() if isinstance(o, cq.Shape)] if isinstance(obj, cq.Workplane) else [obj]
    solids = [s for shape in shapes for s in shape.Solids()]
    if not solids:
        raise ValueError("`result` contains no solid (only faces/wires/edges, or it is empty)")
    count = len(solids)
    if count > 1:
        try:
            fused = solids[0].fuse(*solids[1:]).clean()
            solids = fused.Solids() or solids
        except Exception:
            pass
        if len(solids) > 1:
            solids.sort(key=lambda s: s.Volume(), reverse=True)
            dropped = sum(s.Volume() for s in solids[1:])
            notes.append(f"result has {len(solids)} disconnected solids; kept only the largest and dropped "
                         f"{dropped:.0f} mm3 of detached material. Every feature must touch or overlap the main body.")
        else:
            notes.append(f"result held {count} separate solids that were fused into one; union them in the script.")
    solid = solids[0]
    if not solid.isValid():
        try:
            fixed = solid.fix()
            if fixed.isValid() and fixed.Solids():
                solid = fixed.Solids()[0]
        except Exception:
            pass
        if not solid.isValid():
            notes.append("the solid is not a valid B-rep (self-intersecting or open); simplify the boolean/fillet "
                         "that caused it.")
    return solid, count


def _through_holes(solid) -> int | None:
    from agentic_cad.cadlib import through_holes

    try:
        return through_holes(solid)
    except Exception:
        return None


def measure(solid) -> dict:
    bb = solid.BoundingBox()
    diameters: Counter = Counter()
    for face in solid.Faces():
        if face.geomType() == "CYLINDER":
            try:
                diameters[round(2 * face._geomAdaptor().Cylinder().Radius(), 2)] += 1
            except Exception:
                pass
    return {
        "bbox": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
        "volume": round(solid.Volume(), 2),
        "area": round(solid.Area(), 2),
        "faces": len(solid.Faces()),
        "face_types": dict(Counter(f.geomType() for f in solid.Faces())),
        "cyl_diameters": {str(k): v for k, v in sorted(diameters.items())},
        "valid": bool(solid.isValid()),
        "through_holes": _through_holes(solid),
    }


def tessellate(solid):
    import numpy as np

    bb = solid.BoundingBox()
    tolerance = max(bb.DiagonalLength / 1500, 0.01)
    # 0.1 rad between facets keeps the outline of a round face within 0.13 % of its radius; at 0.35 a cylinder
    # is drawn up to 1.5 % too narrow, which costs a turned part more silhouette match than a real error does
    angular = 0.1
    for _ in range(4):
        verts, tris = solid.tessellate(tolerance, angular)
        if len(tris) <= MAX_TRIANGLES:
            break
        tolerance, angular = tolerance * 2.5, min(0.35, angular * 1.9)
    return np.array([v.toTuple() for v in verts], dtype=float), np.array(tris, dtype=int)


def run(script: Path, out_dir: Path, case_dir: Path | None, stem: str, image_size: int, tile: int,
        base: Path | None = None, bbox: list[float] | None = None) -> dict:
    started = time.time()
    report: dict = {"ok": False, "error": None, "notes": []}
    try:
        import cadquery as cq

        from agentic_cad import render
        from agentic_cad.cadlib import HELPERS

        shown: list = []
        namespace = {"cq": cq, "cadquery": cq, "math": math, "__name__": "__cad__",
                     "show_object": lambda obj, *a, **k: shown.append(obj), **HELPERS}
        if base is not None:   # the solid the script starts from (pipeline engine): `base`
            namespace["base"] = cq.Workplane(obj=cq.importers.importStep(str(base)).val().Solids()[0])
        source = script.read_text()
        try:
            exec(compile(source, script.name, "exec"), namespace)
        except BaseException as exc:  # includes SystemExit raised by generated code
            tb = traceback.extract_tb(exc.__traceback__)
            src_lines = source.splitlines()
            lines = [f"line {f.lineno}: {src_lines[f.lineno - 1].strip() if 0 < f.lineno <= len(src_lines) else ''}"
                     for f in tb if f.filename == script.name]
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["error_where"] = lines[-3:]
            return report

        if isinstance(namespace.get("feature_log"), list):   # per-feature outcome of a compiled feature list
            report["feature_log"] = [str(line)[:300] for line in namespace["feature_log"][:100]]
        solid, count = to_single_solid(find_result(namespace, shown), report["notes"])
        bb = solid.BoundingBox()
        solid = solid.translate(cq.Vector(-bb.xmin, -bb.ymin, -bb.zmin))
        report.update(measure(solid))
        report["solids_in_result"] = count

        step_path = out_dir / f"{stem}.stp"
        cq.exporters.export(cq.Workplane(obj=solid), str(step_path), exportType="STEP")
        reread = cq.importers.importStep(str(step_path)).val()
        if len(reread.Solids()) != 1:
            raise ValueError(f"exported STEP re-imports as {len(reread.Solids())} solids")
        report["step"] = step_path.name

        try:
            verts, tris = tessellate(solid)
            report["triangles"] = int(len(tris))
            refs = {}
            if case_dir is not None:
                for path in sorted((case_dir / "images").glob("view_*.png")):
                    for name in sorted(render.VIEW_NAMES, key=len, reverse=True):
                        if path.stem.endswith(name):
                            refs[name] = path
                            break
            facts_path = case_dir / "facts.json" if case_dir else None
            target_bbox = list(bbox) if bbox else None   # the caller's envelope (read from the brief) wins
            if target_bbox is None and facts_path and facts_path.is_file():
                box = json.loads(facts_path.read_text()).get("bounding_box_mm") or {}
                if all(k in box for k in "xyz"):
                    target_bbox = [float(box["x"]), float(box["y"]), float(box["z"])]
            if target_bbox and refs:
                iou = {}
                for view in render.VIEWS:
                    if view[0] in refs and view[3]:
                        value = render.silhouette_iou(verts, tris, view, render.reference_mask(refs[view[0]], image_size),
                                                      target_bbox)
                        if value is not None:
                            iou[view[0]] = round(value, 4)
                report["iou"] = iou
            iso = {}
            for view in render.VIEWS:      # not part of the rank: only used to choose between hulls
                if view[0] in refs and not view[3]:
                    value = render.outline_iou(verts, tris, view, render.reference_mask(refs[view[0]], 2 * image_size))
                    if value is not None:
                        iso[view[0]] = round(value, 4)
            if iso:
                report["iso_iou"] = iso
            if refs and tile:
                report["sheets"] = [p.name for p in render.comparison_sheets(verts, tris, refs, tile, out_dir, stem)]
        except Exception as exc:  # rendering is feedback only: never lose a good solid over it
            report["notes"].append(f"(internal) view comparison failed: {type(exc).__name__}: {exc}")
        report["ok"] = True
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()[-1500:]
    finally:
        report["seconds"] = round(time.time() - started, 2)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("script", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--case-dir", type=Path, default=None)
    parser.add_argument("--stem", default="candidate")
    parser.add_argument("--image-size", type=int, default=384)
    parser.add_argument("--tile", type=int, default=320)
    parser.add_argument("--base", type=Path, default=None, help="STEP file made available to the script as `base`")
    parser.add_argument("--bbox", default=None, help="target envelope 'x,y,z' in mm; default: the case's facts.json")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    bbox = [float(v) for v in args.bbox.split(",")] if args.bbox else None
    report = run(args.script, args.out_dir, args.case_dir, args.stem, args.image_size, args.tile, args.base, bbox)
    (args.out_dir / f"{args.stem}.json").write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
