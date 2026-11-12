"""Spike: circles a straight view draws that our solid does not, standing over a listed buried hole.

For each case prints the candidate cuts (a cylinder of the drawn diameter from our surface down to
the near end of the listed coaxial hole) and, when a ground truth is given, how much of the material
the cut would remove is not in the true part.

    uv run python tools/seen_mouths/find.py <inputs dir> <our outputs dir> [<ground truth dir>] -- test_case_5 ...
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from agentic_cad import visual_hull as vh
from agentic_cad.seen_mouths import candidates



def load(path: Path, to_origin: bool = False):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.STEPControl import STEPControl_Reader
    reader = STEPControl_Reader()
    reader.ReadFile(str(path))
    reader.TransferRoots()
    shape = reader.OneShape()
    if to_origin:
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, box, False, False)
        low = box.CornerMin()
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(-low.X(), -low.Y(), -low.Z()))
        shape = BRepBuilderAPI_Transform(shape, move, True).Shape()
    return vh.largest_solid(shape)


def excess_share(cut: dict, ours, truth, samples: int = 400) -> tuple[float, float]:
    """Of the points of the cut that lie in our solid: the share that is not in the true part. And the cut's volume."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN
    rng = np.random.default_rng(0)
    axis = cut["axis"]
    other = np.cross(axis, [1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.cross(axis, [0.0, 1.0, 0.0])
    other /= np.linalg.norm(other)
    third = np.cross(axis, other)
    r = cut["radius"] * np.sqrt(rng.random(samples))
    angle = 2 * np.pi * rng.random(samples)
    along = cut["length"] * rng.random(samples)
    points = cut["start"] + np.outer(along, axis) + np.outer(r * np.cos(angle), other) + np.outer(r * np.sin(angle), third)
    inside = []
    for shape in (ours, truth):
        classifier = BRepClass3d_SolidClassifier(shape)
        flags = []
        for point in points:
            classifier.Perform(gp_Pnt(*map(float, point)), 1e-6)
            flags.append(classifier.State() == TopAbs_IN)
        inside.append(np.array(flags))
    in_ours = inside[0]
    share = float((in_ours & ~inside[1]).sum() / max(in_ours.sum(), 1))
    return share, float(np.pi * cut["radius"] ** 2 * cut["length"] * in_ours.mean())


if __name__ == "__main__":
    split = sys.argv.index("--")
    paths, names = sys.argv[1:split], sys.argv[split + 1:]
    inputs, outputs = Path(paths[0]), Path(paths[1])
    truth_dir = Path(paths[2]) if len(paths) > 2 else None
    total = removed = 0.0
    for name in names:
        case_dir = inputs / name
        ours = load(outputs / f"{name}.stp")
        true_volume = vh.read_volume(case_dir)
        found = candidates(ours, case_dir)
        truth = load(truth_dir / f"{name}.stp", to_origin=True) if truth_dir and found else None
        print(f"{name}: {len(found)} mouths; our volume {vh.volume(ours) / true_volume:.3f} x true")
        for cut in found:
            line = (f"    {cut['view']:6s} d {2 * cut['radius']:7.2f} mm, {cut['depth']:6.2f} mm deep, over a listed hole of d {cut['over_hole_d']:.2f}")
            if truth is not None:
                share, volume = excess_share(cut, ours, truth)
                line += f" | {100 * share:3.0f} % of what it removes is not in the part, {100 * volume / true_volume:.1f} % of the true volume"
            else:
                volume = np.pi * cut["radius"] ** 2 * cut["length"]
                line += f" | up to {100 * volume / true_volume:.1f} % of the true volume"
            print(line)
