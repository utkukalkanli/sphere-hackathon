"""Shared helpers: STEP export, validation against facts.json, multi-view render."""
import json
import sys
from collections import Counter
from pathlib import Path

import cadquery as cq

REPO = Path(__file__).resolve().parents[1]
INPUTS = REPO / "artifacts/inputs"
OUTPUTS = REPO / "cad/outputs"   # hand-written reference models, kept out of the submission folder


def solid_of(obj):
    return obj.val() if isinstance(obj, cq.Workplane) else obj


def measure(shape):
    bb = shape.BoundingBox()
    types = Counter(f.geomType() for f in shape.Faces())
    return {
        "bbox": [round(bb.xlen, 2), round(bb.ylen, 2), round(bb.zlen, 2)],
        "bbmin": [round(bb.xmin, 2), round(bb.ymin, 2), round(bb.zmin, 2)],
        "volume": round(shape.Volume(), 1),
        "area": round(shape.Area(), 1),
        "solids": len(shape.Solids()),
        "faces": len(shape.Faces()),
        "edges": len(shape.Edges()),
        "types": dict(types),
        "valid": shape.isValid(),
    }


def report(name, shape):
    facts = json.loads((INPUTS / name / "facts.json").read_text())
    m = measure(shape)
    fb = facts["bounding_box_mm"]
    tgt = {
        "bbox": [fb["x"], fb["y"], fb["z"]],
        "volume": facts["volume_mm3"],
        "area": facts["surface_area_mm2"],
        "solids": facts["solids"],
        "faces": facts["faces"],
        "edges": facts["edges"],
        "types": facts["face_types"],
    }
    print(f"--- {name}  valid={m['valid']}  bbmin={m['bbmin']}")
    for k in ("bbox", "volume", "area", "solids", "faces", "edges", "types"):
        extra = ""
        if k in ("volume", "area"):
            extra = f"  ({100 * (m[k] - tgt[k]) / tgt[k]:+.2f}%)"
        print(f"  {k:7s} got={m[k]}  target={tgt[k]}{extra}")
    return m


def finish(name, obj, render=True):
    """Validate, export to cad/outputs/<name>.stp and optionally render."""
    shape = solid_of(obj)
    report(name, shape)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    out = OUTPUTS / f"{name}.stp"
    cq.exporters.export(cq.Workplane(obj=shape), str(out), exportType="STEP")
    print(f"  wrote {out}")
    if render and "--no-render" not in sys.argv:
        from render import render_views
        print(f"  render {render_views(shape, name)}")
    return shape
