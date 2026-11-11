"""Compare solids with the renders of their test cases, on a drawn coordinate system.

For every case it prints the silhouette IoU in each of the eight views, and writes
`<out>/<case>_<iso view>.png`: the iso render with the bounding box and the X (red), Y (green),
Z (blue) axes from its min corner ticked in mm; in red what the solid covers but the render does not
(still to cut), in blue what the render covers but the solid does not (cut too much).

    uv run python tools/iso_check.py                                   # practice outputs
    uv run python tools/iso_check.py --solids datasets/eval/ground_truth --inputs datasets/eval/inputs --only test_case_1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.gp import gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopoDS import TopoDS_Shape

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from agentic_cad import visual_hull as vh  # noqa: E402
from agentic_cad.views import ViewSet, iou, mesh, rasterize  # noqa: E402


def read_step(path: Path) -> TopoDS_Shape | None:
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        return None
    reader.TransferRoots()
    return reader.OneShape()


def to_origin(shape: TopoDS_Shape) -> TopoDS_Shape:
    """Move the bounding box's min corner to the origin (ground-truth files may sit anywhere)."""
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    corner = box.CornerMin()
    x, y, z = corner.X(), corner.Y(), corner.Z()
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(-x, -y, -z))
    return BRepBuilderAPI_Transform(shape, move, True).Shape()


def check_case(case_dir: Path, solid_path: Path, out_dir: Path) -> dict[str, float]:
    shape = read_step(solid_path)
    if shape is None:
        raise RuntimeError(f"cannot read {solid_path}")
    shape = to_origin(shape)
    views = ViewSet(sorted((case_dir / "images").glob("view_*.png")), vh.read_dimensions(case_dir), shape)
    tris = mesh(shape, views.dims)
    scores = {v.name: round(iou(rasterize(tris, v.camera, v.render.shape), v.render), 4) for v in views.views}
    for name, image in views.iso_overlay(shape).items():
        cv2.putText(image, f"{case_dir.name} {name}  IoU {scores[name]:.3f}   red: still to cut   "
                    "blue: cut too much", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.imwrite(str(out_dir / f"{case_dir.name}_{name}.png"), image)
    return scores


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, default=REPO / "artifacts/inputs")
    parser.add_argument("--solids", type=Path, default=REPO / "artifacts/outputs")
    parser.add_argument("--out", type=Path, default=REPO / "runs/iso_check")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = sorted(args.inputs.glob("test_case_*"), key=lambda p: int(p.name.split("_")[-1]))
    for case_dir in cases:
        solid = args.solids / f"{case_dir.name}.stp"
        if (args.only and case_dir.name not in args.only) or not solid.is_file():
            continue
        scores = check_case(case_dir, solid, args.out)
        print(case_dir.name, " ".join(f"{k}={v:.3f}" for k, v in scores.items()),
              f"min={min(scores.values()):.3f}", flush=True)
    print(f"images in {args.out}")


if __name__ == "__main__":
    main()
