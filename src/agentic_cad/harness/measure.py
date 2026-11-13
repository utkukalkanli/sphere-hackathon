"""Measure a candidate solid against what a case gives: facts.json and the eight views.

No ground truth is read. The local score follows the organisers' formula
(0.30 volume + 0.30 surface + 0.20 dimensions + 0.20 through-holes) with stand-ins for the two
terms that need the true solid: the volume term is the volume ratio times how well the silhouettes
overlap, the surface term the area ratio times the same overlap. It ranks candidates of one case;
it is not the leaderboard score.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.gp import gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import verify
from agentic_cad.hole_count import genus, pinches

AXES = "xyz"
TILE = 512   # px of one view in a sheet sent to the model


def load(path: Path) -> tuple[TopoDS_Shape | None, str]:
    """The one valid solid of a STEP file, or None and why not."""
    try:
        reader = STEPControl_Reader()
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            return None, "the STEP file cannot be read"
        reader.TransferRoots()
        shape = reader.OneShape()
    except Exception as error:
        return None, f"the STEP file cannot be read: {error}"
    if shape is None or shape.IsNull():
        return None, "the file holds no shape"
    solids = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    if len(solids) != 1:
        return None, f"the result has {len(solids)} solids; it must be exactly one (union the bodies, make them overlap)"
    if not BRepCheck_Analyzer(solids[0]).IsValid():
        return None, "the solid is not valid geometry"
    return solids[0], ""


def box(shape: TopoDS_Shape) -> tuple[np.ndarray, np.ndarray]:
    bounds = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bounds, False, False)
    low, high = bounds.CornerMin(), bounds.CornerMax()
    return np.array([low.X(), low.Y(), low.Z()]), np.array([high.X(), high.Y(), high.Z()])


def to_origin(shape: TopoDS_Shape) -> TopoDS_Shape:
    """The shape moved so that its bounding box starts at (0, 0, 0), as the views and facts.json have it."""
    low, _ = box(shape)
    if np.abs(low).max() < 1e-6:
        return shape
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(*(-low).tolist()))
    return BRepBuilderAPI_Transform(shape, move, True).Shape()


def facts_of(case_dir: Path) -> dict:
    return json.loads((case_dir / "facts.json").read_text())


def _ratio(value: float) -> float:
    return min(value, 1.0 / value) if value > 0 else 0.0


def _regions(mask: np.ndarray, name: str, dims: dict[str, float], mm_per_px: float, least: int) -> list[str]:
    """Where the largest patches of `mask` lie, in the part's own millimetres (orthographic views)."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    right, up, _ = verify.camera(name)
    centre = np.array([dims[a] for a in AXES]) / 2
    half = mask.shape[0] / 2
    found = []
    for index in sorted(range(1, count), key=lambda i: -stats[i, cv2.CC_STAT_AREA])[:3]:
        left, top, width, height, area = stats[index]
        if area < least:
            break
        spans = []
        for vector, first, second in ((right, left, left + width), (up, top + height, top)):
            axis = int(np.abs(vector).argmax())
            sign = 1.0 if vector[axis] > 0 else -1.0
            if vector is right:
                ends = [(first - half) * mm_per_px, (second - half) * mm_per_px]
            else:
                ends = [(half - first) * mm_per_px, (half - second) * mm_per_px]
            low, high = sorted(centre[axis] + sign * end for end in ends)
            spans.append(f"{AXES[axis]} {max(low, 0):.0f}..{min(high, 2 * centre[axis]):.0f}")
        found.append(f"{spans[0]}, {spans[1]} mm ({100 * area / max(mask.size, 1):.1f} % of the image)")
    return found


def measure(shape: TopoDS_Shape, case_dir: Path, observed: dict | None = None) -> dict:
    """Everything known about a candidate without the true solid."""
    facts = facts_of(case_dir)
    dims = {a: float(facts["bounding_box_mm"][a]) for a in AXES}
    if observed is None:
        observed = verify.observe(case_dir, dims)
    low, high = box(shape)
    size = high - low
    target = np.array([dims[a] for a in AXES])
    volume, area = verify.measure(shape)
    result = {
        "size": [round(float(v), 2) for v in size], "target_size": [round(float(v), 2) for v in target],
        "volume": volume / facts["volume_mm3"] if facts.get("volume_mm3") else None,
        "area": area / facts["surface_area_mm2"] if facts.get("surface_area_mm2") else None,
        "holes": genus(shape), "target_holes": facts.get("through_holes_genus"),
        "pinches": pinches(shape), "views": {}, "drawings": {},
    }
    if observed:
        mesh = verify.tessellate(shape, 0.3 * min(o.mm_per_px for o in observed.values()))
        for name, view in observed.items():
            drawing = verify.draw(mesh, name, dims, view.mm_per_px, view.mask.shape[0])
            check = verify.compare(view, drawing)
            excess, missing = drawing.mask & ~view.mask, view.mask & ~drawing.mask
            seen = max(int(view.mask.sum()), 1)
            entry = {"silhouette": check.silhouette, "lines": check.lines, "drawn": check.recall, "shown": check.precision,
                     "excess": float(excess.sum() / seen), "missing": float(missing.sum() / seen)}
            if name in verify.ORTHO_VIEWS:
                least = max(int(0.002 * view.mask.size), 30)
                entry["excess_at"] = _regions(excess, name, dims, view.mm_per_px, least)
                entry["missing_at"] = _regions(missing, name, dims, view.mm_per_px, least)
            result["views"][name] = entry
            result["drawings"][name] = drawing
    result.update(score(result))
    return result


def score(m: dict) -> dict:
    """The organisers' four terms, with stand-ins where the true solid would be needed."""
    # the worst extent, on the line through the slide's two points (2 % off loses 10 %, 20 % off loses
    # everything), as tools/score_truth.py has it
    errors = np.abs(np.sort(m["size"]) - np.sort(m["target_size"])) / np.maximum(np.sort(m["target_size"]), 1e-9)
    worst = float(errors.max())
    dimensions = 1.0 - 5.0 * worst if worst <= 0.02 else max(0.0, 0.9 * (0.20 - worst) / 0.18)
    ours, true = m["holes"], m["target_holes"]
    if true is None:
        holes = 1.0
    elif ours == true:
        holes = 1.0
    else:
        holes = min(ours, true) / max(ours, true) if min(ours, true) > 0 else 0.0
    silhouettes = [v["silhouette"] for v in m["views"].values()]
    overlap = float(np.mean(silhouettes)) if silhouettes else 1.0
    # How well the edges agree with the lines drawn in the views: the only sign of what lies inside the
    # outline (a pocket, a step, a boss in the wrong place). Fitted on the 27 solids of three submissions
    # against their leaderboard scores (2026-11-13): mean error 0.036 without it, 0.027 with it; the part
    # the old estimate overrated most (practice 7, 0.938 for a real 0.811) comes out at 0.813.
    lines = [v["lines"] for v in m["views"].values()]
    edges = float(np.mean(lines)) ** 0.5 if lines else 1.0
    volume = (_ratio(m["volume"]) if m["volume"] else 1.0) * overlap * edges ** 0.5
    surface = (_ratio(m["area"]) if m["area"] else 1.0) * overlap * edges
    total = 0.30 * volume + 0.30 * surface + 0.20 * dimensions + 0.20 * holes
    return {"score": total, "terms": {"volume": volume, "surface": surface, "dimensions": dimensions, "holes": holes},
            "overlap": overlap, "edges": float(np.mean(lines)) if lines else 1.0}


def summary(m: dict) -> dict:
    """The numbers of a measurement, without the drawings: what is kept on disk."""
    kept = {k: v for k, v in m.items() if k != "drawings"}
    return json.loads(json.dumps(kept, default=float))


def feedback(m: dict) -> str:
    """The measurement as the model reads it."""
    lines = [f"Estimated score {m['score']:.3f} (volume {m['terms']['volume']:.2f}, surface {m['terms']['surface']:.2f}, "
             f"dimensions {m['terms']['dimensions']:.2f}, through-holes {m['terms']['holes']:.2f})."]
    size, target = m["size"], m["target_size"]
    if max(abs(a - b) / b for a, b in zip(size, target)) > 0.002:
        lines.append(f"- Bounding box {size[0]} x {size[1]} x {size[2]} mm, must be {target[0]} x {target[1]} x {target[2]} (X x Y x Z).")
    else:
        lines.append("- Bounding box: right.")
    if m["volume"]:
        off = 100 * (m["volume"] - 1)
        lines.append(f"- Volume {m['volume']:.3f} of the true one ({off:+.0f} %): "
                     + ("too much material, something is still to be cut away (cavities, pockets, recesses, thinner walls)."
                        if off > 3 else "material is missing." if off < -3 else "right."))
    if m["area"]:
        lines.append(f"- Surface area {m['area']:.3f} of the true one"
                     + (" (too little: inner faces, pockets or details are missing)." if m["area"] < 0.95
                        else " (too much)." if m["area"] > 1.05 else "."))
    if m["target_holes"] is not None:
        lines.append(f"- Through-holes (genus) {m['holes']}, must be {m['target_holes']}."
                     + (" This alone is a fifth of the score." if m["holes"] != m["target_holes"] else ""))
    if m["pinches"]:
        lines.append(f"- {m['pinches']} place(s) where two bodies touch along a single edge or in a point: make them overlap or apart.")
    for name, view in m["views"].items():
        if view["silhouette"] > 0.985:
            continue
        line = (f"- View {name}: outline overlap {view['silhouette']:.3f}; {100 * view['excess']:.1f} % of the part's area is "
                f"material of yours where the view is empty, {100 * view['missing']:.1f} % is missing.")
        if view.get("excess_at"):
            line += " Too much at: " + "; ".join(view["excess_at"]) + "."
        if view.get("missing_at"):
            line += " Missing at: " + "; ".join(view["missing_at"]) + "."
        lines.append(line)
    if all(view["silhouette"] > 0.985 for view in m["views"].values()) and m["views"]:
        lines.append("- All eight outlines fit.")
    if m["views"]:
        lines.append(f"- Edges: {m['edges']:.2f} agreement between your solid's edges and the lines drawn in the views (1.00 = "
                     "every line of the part is an edge of yours and the other way round). This is what shows whether the "
                     "features inside the outline (pockets, steps, bosses, ribs, cavities) are there and in the right place.")
        worst = sorted(m["views"].items(), key=lambda item: item[1]["lines"])[:3]
        for name, view in worst:
            if view["lines"] > 0.9:
                continue
            lines.append(f"  - {name}: {100 * view.get('drawn', 0):.0f} % of the part's lines have an edge of yours, "
                         f"{100 * view.get('shown', 0):.0f} % of your edges lie on a line of the part.")
    return "\n".join(lines)


# -- pictures for the model ---------------------------------------------------------------------------

def _label(image: np.ndarray, text: str) -> np.ndarray:
    cv2.rectangle(image, (0, 0), (image.shape[1], 22), (255, 255, 255), -1)
    cv2.putText(image, text, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    return image


def _png(image: np.ndarray) -> bytes:
    return cv2.imencode(".png", image)[1].tobytes()


def views_sheet(observed: dict) -> bytes:
    """The eight views of the case in one picture, four in a row, each named."""
    tiles = [_label(cv2.resize(view.image[:, :, :3], (512, 512), interpolation=cv2.INTER_AREA), name)
             for name, view in observed.items()]
    while len(tiles) % 4:
        tiles.append(np.full((512, 512, 3), 255, np.uint8))
    return _png(np.vstack([np.hstack(tiles[i:i + 4]) for i in range(0, len(tiles), 4)]))


def _render(drawing, view) -> np.ndarray:
    """The candidate as the camera of `view` sees it: grey by the face's slope, edges dark,
    red where it has material the view has not, blue where the view's part is missing."""
    light = np.array([0.3, -0.5, 0.8])
    light /= np.linalg.norm(light)
    shade = 110 + 130 * np.clip(np.abs(drawing.normal @ light), 0, 1)
    image = np.full(drawing.mask.shape + (3,), 255, np.uint8)
    image[drawing.mask] = shade[drawing.mask][:, None]
    image[drawing.sharp] = (40, 40, 40)
    image[drawing.mask & ~view.mask] = (60, 60, 230)
    image[view.mask & ~drawing.mask] = (230, 120, 60)
    return image


def compare_sheet(m: dict, observed: dict) -> bytes:
    """Every view beside the candidate seen from the same camera."""
    rows = []
    names = [name for name in observed if name in m["drawings"]]
    for start in range(0, len(names), 2):
        row = []
        for name in names[start:start + 2]:
            view = observed[name]
            wanted = cv2.resize(view.image[:, :, :3], (TILE, TILE), interpolation=cv2.INTER_AREA)
            ours = cv2.resize(_render(m["drawings"][name], view), (TILE, TILE), interpolation=cv2.INTER_AREA)
            row += [_label(wanted, f"{name}: the part"), _label(ours, f"{name}: yours")]
        while len(row) < 4:
            row.append(np.full((TILE, TILE, 3), 255, np.uint8))
        rows.append(np.hstack(row))
    return _png(np.vstack(rows))


def compare_views(m: dict, observed: dict) -> dict[str, bytes]:
    """Each view at full size, the part on the left and the candidate on the right, for a closer look."""
    out = {}
    for name, view in observed.items():
        if name in m["drawings"]:
            out[name] = _png(np.hstack([_label(view.image[:, :, :3].copy(), f"{name}: the part"),
                                        _label(_render(m["drawings"][name], view), f"{name}: yours")]))
    return out
