"""Spike, part 1: the depth of the ground truth and of our solid in the six straight views.

For each case writes <out>/<case>.npz with, per view, `truth_<view>` and `ours_<view>` (mm towards
the camera from the centre of the box, NaN outside the part) at SIZE px, and `mm_per_px`.

    uv run python tools/depth_spike/render_depth.py <our outputs dir> <out dir> test_case_236 ...
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "datasets" / "eval"
SIZE = 512


def load(path: Path, to_origin: bool):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    reader.ReadFile(str(path))
    reader.TransferRoots()
    shape = reader.OneShape()
    if to_origin:   # ours has its min corner at the origin
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, box, False, False)
        low = box.CornerMin()
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(-low.X(), -low.Y(), -low.Z()))
        shape = BRepBuilderAPI_Transform(shape, move, True).Shape()
    return shape


def render(job):
    from agentic_cad import verify

    name, ours_dir, out = job
    case = EVAL / "inputs" / name
    box = json.loads((case / "facts.json").read_text())["bounding_box_mm"]
    dims = {k: float(box[k]) for k in "xyz"}
    size = np.array([dims["x"], dims["y"], dims["z"]])
    deflection = float(size.max()) / 2000
    solids = {"truth": load(EVAL / "ground_truth" / f"{name}.stp", True), "ours": load(Path(ours_dir) / f"{name}.stp", False)}
    arrays = {}
    for label, solid in solids.items():
        mesh = verify.tessellate(solid, deflection)
        for view in verify.ORTHO_VIEWS:
            right, up, _ = verify.camera(view)
            half_mm = max(abs(right) @ size, abs(up) @ size) / 2
            mm_per_px = half_mm * 1.04 / (SIZE / 2)
            drawing = verify.draw(mesh, view, dims, mm_per_px, SIZE)
            arrays[f"{label}_{view}"] = np.where(drawing.mask, drawing.depth, np.nan).astype(np.float32)
            arrays[f"mm_per_px_{view}"] = np.float32(mm_per_px)
    np.savez_compressed(Path(out) / f"{name}.npz", size=size, **arrays)
    return name


if __name__ == "__main__":
    ours_dir, out, names = sys.argv[1], sys.argv[2], sys.argv[3:]
    Path(out).mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(8) as pool:
        for name in pool.map(render, [(n, ours_dir, out) for n in names]):
            print("rendered", name, flush=True)
