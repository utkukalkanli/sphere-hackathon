"""`./sections [file.step]`: cross-sections of a solid as one picture, for the agent to see where
material is inside (no view shows that). Default: base.step. Writes sections.png in the workspace:
five cuts across each axis, material dark, each cut labelled with its position in millimetres."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

CUTS = 5
TILE = 300


def picture(grid: np.ndarray, dims: dict[str, float], cell: float) -> np.ndarray:
    rows = []
    # (axis cut, horizontal axis, vertical axis): the same sense as the views (front: x right, z up; top: x right, y up)
    for cut, across, up in ((1, 0, 2), (0, 1, 2), (2, 0, 1)):
        tiles = []
        count = grid.shape[cut]
        for share in np.linspace(0.1, 0.9, CUTS):
            index = min(int(share * count), count - 1)
            plane = np.take(grid, index, axis=cut)             # axes in ascending order of the two that are left
            if across > up:
                plane = plane.T
            image = np.where(plane.T[::-1], 70, 255).astype(np.uint8)   # rows: `up` downwards from its maximum
            scale = (TILE - 30) / max(image.shape)
            image = cv2.resize(image, (max(int(image.shape[1] * scale), 1), max(int(image.shape[0] * scale), 1)),
                               interpolation=cv2.INTER_NEAREST)
            tile = np.full((TILE, TILE), 225, np.uint8)
            tile[26:26 + image.shape[0], 4:4 + image.shape[1]] = image
            label = f"{'xyz'[cut]}={(index + 0.5) * cell:.0f}  ({'xyz'[across]} right, {'xyz'[up]} up)"
            cv2.putText(tile, label, (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, 0, 1, cv2.LINE_AA)
            tiles.append(tile)
        rows.append(np.hstack(tiles))
    return np.vstack(rows)


def main() -> int:
    workspace = Path.cwd()
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else workspace / "base.step"
    if not path.is_file():
        print(f"usage: ./sections [file.step]   ({path} is not a file)")
        return 2
    from agentic_cad.harness import measure as ms
    from agentic_cad.harness.check import _Quiet
    from agentic_cad.hidden_void import occupancy

    with _Quiet():
        shape, why = ms.load(path)
    if shape is None:
        print(why)
        return 1
    facts = json.loads((workspace / "facts.json").read_text())
    dims = {a: float(facts["bounding_box_mm"][a]) for a in "xyz"}
    cell = max(dims.values()) / 160
    grid = occupancy(ms.to_origin(shape), dims, cell)
    cv2.imwrite(str(workspace / "sections.png"), picture(grid, dims, cell))
    print(f"sections.png: {CUTS} cuts across y (top row), x (middle row) and z (bottom row) of {path.name}; "
          f"material is dark, one cell is {cell:.1f} mm. Filled share of the box: {grid.mean():.2f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
