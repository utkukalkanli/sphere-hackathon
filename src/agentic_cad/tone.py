"""The tone a renderer gives a face, as a function of which way the face looks from the camera.

The light sits with the camera and is the same in every view and for every part, so one table per
renderer says it all (docs/memory/knowledge.md): the median tone for each direction of the normal in
the camera's frame (x to the right of the image, y up, z towards the camera), on a grid over (x, y).
The tables were measured with tools/check_normals.py --table and are stored next to this file:

- "organisers": the renderer of the real inputs, measured on the 9 practice parts;
- "eval": our own renderer of datasets/eval, measured on 24 of its parts.

`choose` picks the table that explains the views of a case, given the solid carved so far.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from agentic_cad import verify

GRID = 41          # cells along x and along y of the normal
MIN_CELL = 30      # a cell measured on fewer pixels is not trusted
FILE = Path(__file__).with_name("tone_tables.npz")
DEFAULT = "organisers"


def _cell_centres(grid: int) -> np.ndarray:
    centre = (np.arange(grid) + 0.5) / grid * 2 - 1
    x, y = np.meshgrid(centre, centre)
    z = np.sqrt(np.where(x ** 2 + y ** 2 <= 1, 1 - x ** 2 - y ** 2, np.nan))
    return np.stack([x, y, z], axis=2)


def build(normal: np.ndarray, tone: np.ndarray, grid: int = GRID) -> dict[str, np.ndarray]:
    """A table from pixels: `normal` (n, 3) in the camera's frame, `tone` (n,). Also the linear model
    `tone = c0 + c . normal` fitted to the cells, used where a cell has no measurement."""
    index = np.clip(((normal[:, :2] + 1) / 2 * grid).astype(int), 0, grid - 1)
    cell = index[:, 1] * grid + index[:, 0]
    order = np.argsort(cell, kind="stable")
    cell, tone = cell[order], np.asarray(tone, dtype=float)[order]
    table = np.full(grid * grid, np.nan)
    starts = np.r_[0, np.nonzero(np.diff(cell))[0] + 1, len(cell)]
    for a, b in zip(starts[:-1], starts[1:]):
        if b - a >= MIN_CELL:
            table[cell[a]] = np.median(tone[a:b])
    centres = _cell_centres(grid).reshape(-1, 3)
    known = ~np.isnan(table) & ~np.isnan(centres[:, 2])
    design = np.c_[np.ones(int(known.sum())), centres[known]]
    linear = np.linalg.lstsq(design, table[known], rcond=None)[0]
    return {"table": table.reshape(grid, grid), "linear": linear}


@dataclass(frozen=True, eq=False)
class Table:
    name: str
    filled: np.ndarray   # (grid, grid) tones, the cells without a measurement filled in from the linear model

    def tone(self, normal: np.ndarray) -> np.ndarray:
        """The tone of faces with these normals (..., 3) in the camera's frame, read between the cells."""
        grid = self.filled.shape[0]
        position = (np.clip(normal[..., :2], -1, 1) + 1) / 2 * grid - 0.5
        low = np.clip(np.floor(position).astype(int), 0, grid - 2)
        weight = np.clip(position - low, 0, 1)
        x, y, wx, wy = low[..., 0], low[..., 1], weight[..., 0], weight[..., 1]
        return ((1 - wy) * ((1 - wx) * self.filled[y, x] + wx * self.filled[y, x + 1])
                + wy * ((1 - wx) * self.filled[y + 1, x] + wx * self.filled[y + 1, x + 1]))


def _fill(table: np.ndarray, model: np.ndarray, rounds: int = 3) -> np.ndarray:
    """Cells without a measurement take the mean of their measured neighbours (a few cells far), and
    beyond that the linear model."""
    for _ in range(rounds):
        missing = np.isnan(table)
        if not missing.any():
            break
        known = (~missing).astype(np.float32)
        total = cv2.blur(np.where(missing, 0, table).astype(np.float32), (3, 3), borderType=cv2.BORDER_CONSTANT)
        share = cv2.blur(known, (3, 3), borderType=cv2.BORDER_CONSTANT)
        table = np.where(missing & (share > 0), total / np.maximum(share, 1e-9), table)
    return np.where(np.isnan(table), model, table)


@lru_cache(maxsize=1)
def tables() -> dict[str, Table]:
    result = {}
    try:
        with np.load(FILE) as stored:
            for name in sorted({key.split("_", 1)[0] for key in stored.files}):
                table, linear = stored[f"{name}_table"], stored[f"{name}_linear"]
                centres = _cell_centres(table.shape[0])
                model = linear[0] + np.nan_to_num(centres, nan=0.0) @ linear[1:]
                result[name] = Table(name, _fill(table.astype(float), model))
    except Exception:
        pass
    return result


def in_camera(normal: np.ndarray, view: str) -> np.ndarray:
    """Normals in the frame of the solid (..., 3), turned into the camera's frame of a view."""
    return normal @ np.stack(verify.camera(view)).T


def misfit(table: Table, observed: dict, drawings, per_view: int = 4000) -> float:
    """Median difference between the tones of the views and what the table says about the solid's
    faces there. `drawings[name]` is the solid drawn from the camera of view `name`."""
    differences = []
    rng = np.random.default_rng(0)
    for name, seen in observed.items():
        drawing = drawings[name]
        blocked = cv2.dilate((seen.lines | drawing.edges).astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        rows, cols = np.nonzero(seen.mask & drawing.mask & ~blocked)
        if len(rows) > per_view:
            keep = rng.choice(len(rows), per_view, replace=False)
            rows, cols = rows[keep], cols[keep]
        if len(rows):
            tones = seen.image[rows, cols].max(axis=1).astype(float)
            differences.append(np.abs(tones - table.tone(in_camera(drawing.normal[rows, cols], name))))
    return float(np.median(np.concatenate(differences))) if differences else float("inf")


def choose(observed: dict, drawings) -> Table | None:
    """The table of the renderer that made these views: the organisers' one, unless another explains
    the solid's own faces clearly better (less than half the misfit)."""
    known = tables()
    if DEFAULT not in known:
        return next(iter(known.values()), None)
    best, best_misfit = known[DEFAULT], misfit(known[DEFAULT], observed, drawings)
    for name, table in known.items():
        if name != DEFAULT:
            value = misfit(table, observed, drawings)
            if value < 0.5 * best_misfit:
                best, best_misfit = table, value
    return best
