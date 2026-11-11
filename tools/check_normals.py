"""How much does the tone of a pixel say about the direction of the face under it?

    uv run python tools/check_normals.py                      # the 24 fixed eval cases, normals from the ground truth
    uv run python tools/check_normals.py --only test_case_5   # chosen eval cases
    uv run python tools/check_normals.py --practice           # the 9 practice parts, normals from our own solid
    uv run python tools/check_normals.py --save DIR           # also write the fitted tone maps as images

For every view the solid is drawn from the view's camera, which gives a normal per pixel, in the
camera's frame: x to the right of the image, y up, z towards the camera. Pixels on or next to a line
are left out. Two models of `tone = f(normal)` are fitted and their misfit is reported:

- linear: tone = c0 + c . normal (a constant plus one distant light), fitted to the cells of the map;
- map: the median tone of every cell of a grid over (x, y) of the normal, with no assumption on the
  light. Cells with few pixels fall back on the linear model.

"other cases" means the model was fitted without the case it is tested on, so it tells whether one
model serves parts it has never seen. "own" is fitted on the case itself.

The practice set has no ground truth: there the normals are those of our solid after steps 1 to 4
(cached by tools/check_shading.py in runs/_shading_cache), which are wrong wherever the solid is.
Read the median misfit there, not the rms.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
EVAL = ROOT / "datasets" / "eval"
CACHE = ROOT / "runs" / "_shading_cache"

PER_VIEW = 20000     # pixels kept per view
CLEAR_PX = 3         # pixels this close to a line, or to a face boundary of the drawn solid, are left out
ANGULAR = 0.05       # radians between neighbouring triangles of the drawn solid: normals good to 1.5 degrees
GRID = 21            # cells of the tone map along x and along y of the normal
MIN_CELL = 30        # a cell with fewer pixels is not trusted


def truth(name: str):
    """The ground truth of an eval case, with its bounding-box min corner at the origin like our solids."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    reader.ReadFile(str(EVAL / "ground_truth" / f"{name}.stp"))
    reader.TransferRoots()
    shape = reader.OneShape()
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    low = box.CornerMin()
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(-low.X(), -low.Y(), -low.Z()))
    return BRepBuilderAPI_Transform(shape, move, True).Shape()


def collect(job: dict) -> dict:
    """Per view of one case: normals in the camera's frame and the tones seen there."""
    os.environ["TRACE"] = "0"
    import cv2

    from agentic_cad import shading, verify
    from agentic_cad import visual_hull as vh

    name, practice = job["name"], job["practice"]
    try:
        case = (ROOT / "artifacts" / "inputs" if practice else EVAL / "inputs") / name
        dims = dict(zip("xyz", vh.read_dimensions(case)))
        if practice:
            path = CACHE / f"practice_{name}.brep"
            if not path.is_file():
                return {"name": name, "error": f"{path} is missing: run tools/check_shading.py --practice first"}
            solid = vh.largest_solid(vh.read_brep(str(path)))
        else:
            solid = truth(name)
        observed = verify.observe(case, dims)
        mesh = verify.tessellate(solid, 0.3 * min(o.mm_per_px for o in observed.values()), ANGULAR)
        rng = np.random.default_rng(0)
        kernel = np.ones((2 * CLEAR_PX + 1, 2 * CLEAR_PX + 1), np.uint8)
        views = {}
        for view, seen in observed.items():
            drawing = verify.draw(mesh, view, dims, seen.mm_per_px, seen.mask.shape[0])
            blocked = cv2.dilate((seen.lines | drawing.edges).astype(np.uint8), kernel) > 0
            rows, cols = np.nonzero(seen.mask & drawing.mask & ~blocked)
            if len(rows) > PER_VIEW:
                keep = rng.choice(len(rows), PER_VIEW, replace=False)
                rows, cols = rows[keep], cols[keep]
            frame = np.stack(verify.camera(view))  # right, up, towards the camera
            views[view] = {"normal": (drawing.normal[rows, cols] @ frame.T).astype(np.float32),
                           "tone": shading.tone_of(seen.image)[rows, cols].astype(np.float32)}
        return {"name": name, "views": views}
    except Exception as error:
        import traceback
        return {"name": name, "error": repr(error), "trace": traceback.format_exc()}


# -- the two models ------------------------------------------------------------------------------------

def fit_linear(normal: np.ndarray, tone: np.ndarray) -> np.ndarray:
    """c0, cx, cy, cz of tone = c0 + c . normal."""
    design = np.c_[np.ones(len(normal)), normal]
    return np.linalg.lstsq(design, tone, rcond=None)[0]


def cell_normals() -> np.ndarray:
    """The normal at the centre of every cell of the map (NaN outside the unit circle)."""
    centre = (np.arange(GRID) + 0.5) / GRID * 2 - 1
    x, y = np.meshgrid(centre, centre)
    z = np.sqrt(np.where(x ** 2 + y ** 2 <= 1, 1 - x ** 2 - y ** 2, np.nan))
    return np.stack([x, y, z], axis=2).reshape(-1, 3)


def linear(coefficients: np.ndarray, normal: np.ndarray) -> np.ndarray:
    return coefficients[0] + normal @ coefficients[1:]


def _cell(normal: np.ndarray) -> np.ndarray:
    index = np.clip(((normal[:, :2] + 1) / 2 * GRID).astype(int), 0, GRID - 1)
    return index[:, 1] * GRID + index[:, 0]


def fit_map(normal: np.ndarray, tone: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median tone per cell of (x, y) of the normal; NaN where a cell has too few pixels."""
    cell = _cell(normal)
    order = np.argsort(cell, kind="stable")
    cell, tone = cell[order], tone[order]
    table = np.full(GRID * GRID, np.nan)
    starts = np.r_[0, np.nonzero(np.diff(cell))[0] + 1, len(cell)]
    for a, b in zip(starts[:-1], starts[1:]):
        if b - a >= MIN_CELL:
            table[cell[a]] = np.median(tone[a:b])
    # the linear model is fitted to the cells, not the pixels: most pixels face the camera, and
    # they would leave the tilted directions without a say
    centres = cell_normals()
    known = ~np.isnan(table) & ~np.isnan(centres[:, 2])
    coefficients = fit_linear(centres[known], table[known]) if known.sum() >= 4 else fit_linear(normal, tone)
    return table, coefficients


def mapped(model: tuple[np.ndarray, np.ndarray], normal: np.ndarray) -> np.ndarray:
    table, coefficients = model
    value = table[_cell(normal)]
    return np.where(np.isnan(value), linear(coefficients, normal), value)


def stack(rows: list[dict], views=None) -> tuple[np.ndarray, np.ndarray]:
    parts = [v for r in rows for name, v in r["views"].items() if views is None or name in views]
    if not parts:
        return np.zeros((0, 3), np.float32), np.zeros(0, np.float32)
    return np.concatenate([p["normal"] for p in parts]), np.concatenate([p["tone"] for p in parts])


def rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(values ** 2))) if len(values) else float("nan")


def median(values: np.ndarray) -> float:
    return float(np.median(np.abs(values))) if len(values) else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", nargs="*", metavar="test_case_<n>")
    parser.add_argument("--practice", action="store_true")
    parser.add_argument("--save", metavar="DIR", help="write the tone map of all cases here as an image")
    parser.add_argument("--table", metavar="FILE.npz", help="write the tone map of all cases, as the agent reads it "
                        "(src/agentic_cad/tone.py), to this file")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    import eval_hull
    from agentic_cad import verify

    if args.practice:
        names = args.only or [f"test_case_{n}" for n in range(1, 10)]
    else:
        with open(EVAL / "index.csv", newline="") as f:
            tiers = {f"test_case_{row['n']}": row["tier"] for row in csv.DictReader(f)}
        names = args.only or eval_hull.sample(tiers, 8)
    jobs = [{"name": n, "practice": args.practice} for n in names]
    with ProcessPoolExecutor(args.workers) as pool:
        rows = list(pool.map(collect, jobs))
    for row in rows:
        if "error" in row:
            print(f"{row['name']:14}FAILED {row['error']}\n{row.get('trace', '')}")
    rows = [r for r in rows if "error" not in r and r["views"]]
    if not rows:
        return

    print("misfit of the tone in grey levels (0..255): rms / median")
    print(f"{'case':14}{'pixels':>8}{'tilted':>8} | {'linear, other cases':>20}{'map, other cases':>18}{'linear, own':>14}")
    everything = {"linear": [], "map": [], "own": []}
    by_view = {view: [] for view in verify.CAMERAS}
    for row in rows:
        normal, tone = stack([row])
        others = [r for r in rows if r is not row] or [row]
        model = fit_map(*stack(others))
        own = fit_linear(normal, tone)
        misfit = {"linear": tone - linear(model[1], normal), "map": tone - mapped(model, normal),
                  "own": tone - linear(own, normal)}
        for key, values in misfit.items():
            everything[key].append(values)
        for view, data in row["views"].items():
            by_view[view].append(data["tone"] - mapped(model, data["normal"]))
        tilted = float((normal[:, 2] < 0.985).mean())  # more than 10 degrees away from the camera
        print(f"{row['name']:14}{len(tone):>8}{tilted:>8.0%} | " + "".join(
            f"{rms(misfit[k]):>{w}.1f} /{median(misfit[k]):>5.1f}" for k, w in (("linear", 13), ("map", 11), ("own", 7))))
    print(f"{'all':14}{'':>16} | " + "".join(
        f"{rms(np.concatenate(everything[k])):>{w}.1f} /{median(np.concatenate(everything[k])):>5.1f}"
        for k, w in (("linear", 13), ("map", 11), ("own", 7))))

    print("\nmap fitted on the other cases, by view (the same model in the camera's frame for all of them):")
    for view, values in by_view.items():
        if values:
            values = np.concatenate(values)
            print(f"  {view:22}{rms(values):>6.1f} /{median(values):>5.1f}")

    normal, tone = stack(rows)
    table, coefficients = fit_map(normal, tone)
    c0, cx, cy, cz = coefficients
    light = coefficients[1:] / max(float(np.linalg.norm(coefficients[1:])), 1e-9)
    print(f"\nlinear model of all cases: tone = {c0:.0f} {cx:+.0f} x {cy:+.0f} y {cz:+.0f} z"
          f"   (light from x {light[0]:+.2f}, y {light[1]:+.2f}, z {light[2]:+.2f})")
    centres = cell_normals()
    facing = table[(GRID // 2) * GRID + GRID // 2]
    print(f"tone of a face looking at the camera: {facing:.0f}. Tilted away from it, by the map:")
    tilt = np.degrees(np.arccos(np.clip(centres[:, 2], -1, 1)))
    names = {"right": (1, 0), "up": (0, 1), "left": (-1, 0), "down": (0, -1)}
    for low, high in ((5, 15), (15, 30), (30, 50), (50, 75)):
        cells = (tilt >= low) & (tilt < high) & ~np.isnan(table)
        parts = []
        for label, (dx, dy) in names.items():
            towards = cells & (centres[:, 0] * dx + centres[:, 1] * dy >= 0.9 * np.hypot(centres[:, 0], centres[:, 1]))
            parts.append(f"{label} {np.nanmedian(table[towards]):.0f}" if towards.any() else f"{label} -")
        print(f"  {low:>2} to {high} degrees: " + ", ".join(parts))
    filled = ~np.isnan(table)
    inside = ~np.isnan(centres[:, 2])
    print(f"cells of the map with enough pixels: {int((filled & inside).sum())} of {int(inside.sum())} "
          f"directions a camera can see")
    for label, views in (("straight views", verify.ORTHO_VIEWS),
                         ("isometric views", tuple(v for v in verify.CAMERAS if v not in verify.ORTHO_VIEWS))):
        n, t = stack(rows, views)
        if len(t) >= 100:
            c = fit_map(n, t)[1]
            print(f"linear model of the {label} alone: tone = {c[0]:.0f} {c[1]:+.0f} x {c[2]:+.0f} y {c[3]:+.0f} z")

    if args.table:
        from agentic_cad import tone as tone_module
        fine = tone_module.build(normal, tone)
        np.savez_compressed(args.table, **fine)
        print(f"tone map for the agent written to {args.table}: {int((~np.isnan(fine['table'])).sum())} cells filled")
    if args.save:
        import cv2
        out = Path(args.save)
        out.mkdir(parents=True, exist_ok=True)
        picture = np.where(np.isnan(table), 0, table).reshape(GRID, GRID)[::-1]  # y of the normal upwards
        picture = cv2.resize(picture.astype(np.uint8), (GRID * 24, GRID * 24), interpolation=cv2.INTER_NEAREST)
        target = out / f"tone_map_{'practice' if args.practice else 'eval'}.png"
        cv2.imwrite(str(target), picture)
        np.save(out / f"tone_map_{'practice' if args.practice else 'eval'}.npy", table.reshape(GRID, GRID))
        print(f"tone map written to {target} (x of the normal to the right, y upwards, black: no data)")


if __name__ == "__main__":
    main()
