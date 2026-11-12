"""Score the agent on the local eval set (datasets/eval) against its ground-truth STEP files.

    uv run python scripts/eval.py --tier simple --limit 20                 # run the agent, then score
    uv run python scripts/eval.py --tier simple --limit 20 --engine legacy # same subset, other engine
    uv run python scripts/eval.py --score-only runs/eval_20261111-120000   # re-score an existing run

The agent runs through scripts/main.py with tracing on, so the run shows up in the dashboard (ui/server.py)
under runs/<run id>. Each output is then compared with datasets/eval/ground_truth/<case>.stp, both moved
to their bounding-box minimum corner, on the four terms of the organisers' formula (docs/scoring.md):

    score = 0.30 * volume + 0.30 * surface + 0.20 * dimensions + 0.20 * holes

    volume      volume IoU on a voxel grid, volume(A n B) / volume(A u B)
    surface     F-score of points sampled on the two surfaces, averaged over distance thresholds from 0.5 % to
                10 % of the part size (the ground truth's bounding-box diagonal)
    dimensions  per bounding-box extent: 1 at no error, 0.9 at 2 % off, 0 at 20 % off and beyond; mean of the three
    holes       through-holes (genus, from the B-rep topology) against the ground truth's

The organisers' scorer is not in this repository, so the curves behind these terms are our reading of their
slide, and the pair is not rotated into alignment as theirs is: an output in another orientation scores lower
here than it will there. Results go to <run dir>/scores.csv and a per-tier summary is printed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL = REPO_ROOT / "datasets" / "eval"
RUNS = REPO_ROOT / "runs"
BUILD_STEPS = ("build attempt", "attempt ", "hull ", "facts bores", "round ")   # trace steps that build a solid
PAIR_TIMEOUT_S = 180   # a comparison that hangs must not stall the eval
WEIGHTS = {"iou": 0.30, "surface": 0.30, "dims": 0.20, "holes": 0.20}   # docs/scoring.md
SURFACE_THRESHOLDS = (0.005, 0.01, 0.02, 0.05, 0.10)   # fractions of the part size
SURFACE_SAMPLES = 20000       # points whose distance to the other surface is measured
SURFACE_REFERENCE = 400000    # points standing in for that other surface: dense enough that their spacing is
                              # well under the smallest threshold, or a part would not even match itself
FAILED = {"iou": 0.0, "surface": 0.0, "dims": 0.0, "holes": 0.0, "score": 0.0}   # no usable output scores zero


def read_index() -> dict[str, dict]:
    with open(EVAL / "index.csv", newline="") as f:
        return {f"test_case_{row['n']}": row for row in csv.DictReader(f)}


def select_cases(index: dict[str, dict], tiers: list[str] | None, only: list[str] | None, limit: int | None,
                 offset: int) -> list[str]:
    names = [n for n in index if (EVAL / "inputs" / n / "description.md").is_file()]
    names.sort(key=lambda n: int(n.rsplit("_", 1)[1]))
    if tiers:
        names = [n for n in names if index[n].get("tier") in tiers]
    if only:
        wanted = set(only)
        names = [n for n in names if n in wanted]
    names = names[offset:]
    return names[:limit] if limit else names


# -- comparing one pair (runs in a subprocess: OCC booleans can crash or hang) ----------------------

def compare_pair(output: Path, truth: Path) -> dict:
    import cadquery as cq

    def load(path: Path):
        shape = cq.importers.importStep(str(path)).val()
        solids = shape.Solids()
        if len(solids) != 1:
            raise ValueError(f"{path.name}: {len(solids)} solids")
        solid = solids[0]
        bb = solid.BoundingBox()
        return solid.translate(cq.Vector(-bb.xmin, -bb.ymin, -bb.zmin))

    a, b = load(output), load(truth)
    bba, bbb = a.BoundingBox(), b.BoundingBox()
    box_a = [bba.xlen, bba.ylen, bba.zlen]
    box_b = [bbb.xlen, bbb.ylen, bbb.zlen]
    va, vb = a.Volume(), b.Volume()
    result = {"valid": True, "bbox_err": max(abs(x - y) / y for x, y in zip(box_a, box_b) if y),
              "volume_err": va / vb - 1.0 if vb else None, "bbox": [round(v, 2) for v in box_a],
              "truth_bbox": [round(v, 2) for v in box_b]}
    # Volume IoU on a voxel grid. OCC booleans between a candidate and its ground truth are not a reliable
    # measurement: the two solids share many near-coincident faces, and `fuse` / `common` then return empty,
    # partial or reversed shapes without raising (seen on 10 of 60 parts).
    result["iou"] = voxel_iou(a, b)
    result["method"] = "voxel"
    result["surface"] = surface_match(a, b)
    result["dims"] = dimension_score(box_a, box_b)
    result["genus"], result["truth_genus"] = genus(a), genus(b)
    result["holes"] = hole_score(result["genus"], result["truth_genus"])
    result["score"] = sum(weight * result[term] for term, weight in WEIGHTS.items())
    return result


def _sample_surface(solid, count: int, tolerance: float, seed: int):
    """`count` points spread evenly by area over the tessellated surface."""
    import numpy as np

    verts, tris = solid.tessellate(tolerance, 0.3)
    v = np.array([p.toTuple() for p in verts], dtype=float)
    a, b, c = (v[np.array(tris, dtype=int)[:, k]] for k in range(3))
    areas = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(areas), size=count, p=areas / areas.sum())
    u, w = rng.random(count), rng.random(count)
    flip = u + w > 1
    u[flip], w[flip] = 1 - u[flip], 1 - w[flip]
    return a[pick] + u[:, None] * (b[pick] - a[pick]) + w[:, None] * (c[pick] - a[pick])


def surface_match(a, b) -> float:
    """How much of each surface lies on the other one: at every threshold, the F-score of the share of output
    points within that distance of the truth surface (precision) and the reverse (recall); mean over thresholds."""
    import numpy as np
    from scipy.spatial import cKDTree

    size = b.BoundingBox().DiagonalLength
    tolerance = size * SURFACE_THRESHOLDS[0] / 10
    a_to_b = cKDTree(_sample_surface(b, SURFACE_REFERENCE, tolerance, seed=1)).query(
        _sample_surface(a, SURFACE_SAMPLES, tolerance, seed=0))[0]
    b_to_a = cKDTree(_sample_surface(a, SURFACE_REFERENCE, tolerance, seed=2)).query(
        _sample_surface(b, SURFACE_SAMPLES, tolerance, seed=3))[0]
    scores = []
    for fraction in SURFACE_THRESHOLDS:
        precision = float(np.mean(a_to_b <= fraction * size))
        recall = float(np.mean(b_to_a <= fraction * size))
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return float(np.mean(scores))


def dimension_score(box: list[float], truth_box: list[float]) -> float:
    """Mean over the three extents (compared largest with largest, so a rotated part is not punished here) of a
    curve through the two points on the slide: 2 % off loses 10 %, 20 % off loses everything."""
    def one(value: float, truth: float) -> float:
        if not truth:
            return 0.0
        err = abs(value - truth) / truth
        if err <= 0.02:
            return 1.0 - 5.0 * err
        return max(0.0, 0.9 * (0.20 - err) / 0.18)

    return sum(one(x, y) for x, y in zip(sorted(box), sorted(truth_box))) / 3


def genus(solid) -> int:
    """Number of through-holes, from the B-rep topology, as in facts.json `through_holes_genus`."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from agentic_cad.cadlib import through_holes

    return through_holes(solid)


def hole_score(count: int, truth: int) -> float:
    """1 when the count is right; every missing or extra through-hole costs one share of the truth's holes."""
    return max(0.0, 1.0 - abs(count - truth) / max(truth, 1))


def _occupancy(solid, origin, cell: float, shape: tuple[int, int, int]):
    """Which cells of the grid lie inside the solid: one ray along Z through every (x, y) cell centre, crossings
    with the tessellated surface counted in pairs. Rays with an odd number of crossings (a hit exactly on an
    edge) are reported as invalid instead of guessed."""
    import numpy as np

    tolerance = max(cell / 4, 1e-3)
    verts, tris = solid.tessellate(tolerance, 0.3)
    v = np.array([p.toTuple() for p in verts], dtype=float) - origin
    t = np.array(tris, dtype=int)
    nx, ny, nz = shape
    # cell centres, nudged off the round coordinates that CAD edges tend to sit on
    xs = (np.arange(nx) + 0.5) * cell + 1.37e-4 * cell
    ys = (np.arange(ny) + 0.5) * cell + 2.11e-4 * cell
    hit_i, hit_j, hit_z = [], [], []
    for a, b, c in v[t]:
        i0, i1 = np.searchsorted(xs, [min(a[0], b[0], c[0]), max(a[0], b[0], c[0])])
        j0, j1 = np.searchsorted(ys, [min(a[1], b[1], c[1]), max(a[1], b[1], c[1])])
        if i0 == i1 or j0 == j1:
            continue
        det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(det) < 1e-14:
            continue
        gx, gy = np.meshgrid(xs[i0:i1], ys[j0:j1], indexing="ij")
        w0 = ((b[1] - c[1]) * (gx - c[0]) + (c[0] - b[0]) * (gy - c[1])) / det
        w1 = ((c[1] - a[1]) * (gx - c[0]) + (a[0] - c[0]) * (gy - c[1])) / det
        w2 = 1.0 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        i, j = np.nonzero(inside)
        hit_i.append(i0 + i)
        hit_j.append(j0 + j)
        hit_z.append((w0 * a[2] + w1 * b[2] + w2 * c[2])[i, j])
    occupied = np.zeros(shape, dtype=bool)
    valid = np.ones((nx, ny), dtype=bool)
    if not hit_i:
        return occupied, valid
    column = np.concatenate(hit_i) * ny + np.concatenate(hit_j)
    z = np.concatenate(hit_z)
    order = np.lexsort((z, column))
    column, z = column[order], z[order]
    start = np.flatnonzero(np.r_[True, column[1:] != column[:-1]])
    count = np.diff(np.r_[start, len(column)])
    odd = np.repeat(count % 2 == 1, count)
    valid.reshape(-1)[column[odd]] = False
    column, z = column[~odd], z[~odd]
    # crossings come in (entry, exit) pairs along each column; mark the cells between them
    zc = (np.arange(nz) + 0.5) * cell
    steps = np.zeros((nx * ny, nz + 1), dtype=np.int32)
    np.add.at(steps, (column[0::2], np.searchsorted(zc, z[0::2])), 1)
    np.add.at(steps, (column[1::2], np.searchsorted(zc, z[1::2])), -1)
    occupied = (np.cumsum(steps, axis=1)[:, :nz] > 0).reshape(shape)
    return occupied, valid


def voxel_iou(a, b, cells: int = 160) -> float:
    """Volume IoU of two solids that both sit with their bounding-box min corner at the origin."""
    import numpy as np

    bba, bbb = a.BoundingBox(), b.BoundingBox()
    extent = np.array([max(bba.xmax, bbb.xmax), max(bba.ymax, bbb.ymax), max(bba.zmax, bbb.zmax)], dtype=float)
    cell = float(extent.max()) / cells
    shape = tuple(int(n) for n in np.maximum(1, np.ceil(extent / cell)))
    origin = np.zeros(3)
    occ_a, ok_a = _occupancy(a, origin, cell, shape)
    occ_b, ok_b = _occupancy(b, origin, cell, shape)
    ok = (ok_a & ok_b)[:, :, None]
    union = int(((occ_a | occ_b) & ok).sum())
    return float(((occ_a & occ_b) & ok).sum() / union) if union else 0.0


def score_pair(output: Path, truth: Path) -> dict:
    if not output.is_file():
        return {"valid": False, "error": "no output file", **FAILED}
    command = [sys.executable, str(Path(__file__).resolve()), "--pair", str(output), str(truth)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=PAIR_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"valid": None, "error": f"comparison timed out after {PAIR_TIMEOUT_S} s", "iou": None}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"valid": False, "error": (proc.stderr or proc.stdout).strip()[-300:] or f"exit {proc.returncode}",
                **FAILED}


# -- running and scoring ------------------------------------------------------------------------

def run_agent(run_dir: Path, names: list[str], engine: str, budget_s: float | None, extra_env: dict) -> float:
    env = {**os.environ, **extra_env, "AGENT_ENGINE": engine, "TRACE": "1", "TRACE_DIR": str(RUNS),
           "TRACE_RUN_ID": run_dir.name}
    if budget_s:
        env["AGENT_TIME_BUDGET_S"] = str(budget_s)
    command = [sys.executable, str(REPO_ROOT / "scripts" / "main.py"), "--input_dir", str(EVAL / "inputs"),
               "--output_dir", str(run_dir / "outputs"), "--only", *names]
    started = time.time()
    subprocess.run(command, env=env, cwd=REPO_ROOT, check=False)
    return time.time() - started


def block_baseline(name: str, run_dir: Path) -> Path | None:
    """The agent's fallback: a plain block of the envelope in facts.json. Every engine must beat its IoU."""
    facts_path = EVAL / "inputs" / name / "facts.json"
    if not facts_path.is_file():
        return None
    box = json.loads(facts_path.read_text()).get("bounding_box_mm") or {}
    if not all(k in box for k in "xyz"):
        return None
    path = run_dir / "baseline" / f"{name}.stp"
    if not path.is_file():
        import cadquery as cq
        path.parent.mkdir(parents=True, exist_ok=True)
        cq.exporters.export(cq.Workplane().box(float(box["x"]), float(box["y"]), float(box["z"]), centered=False),
                            str(path), exportType="STEP")
    return path


def score_run(run_dir: Path, names: list[str], index: dict[str, dict]) -> list[dict]:
    def score(name: str) -> dict:
        output = run_dir / "outputs" / f"{name}.stp"
        truth = EVAL / "ground_truth" / f"{name}.stp"
        result = score_pair(output, truth) if truth.is_file() else {"valid": None, "error": "no ground truth"}
        row = {"case": name, "tier": index.get(name, {}).get("tier", ""), "written": output.is_file(),
               "valid": result.get("valid"), "score": result.get("score"), "iou": result.get("iou"),
               "surface": result.get("surface"), "dims": result.get("dims"), "holes": result.get("holes"),
               "genus": result.get("genus"), "truth_genus": result.get("truth_genus"),
               "bbox_err": result.get("bbox_err"), "volume_err": result.get("volume_err"),
               "error": result.get("error") or result.get("iou_error", "")}
        block = block_baseline(name, run_dir) if truth.is_file() else None
        block_result = score_pair(block, truth) if block else {}
        row["block_iou"], row["block_score"] = block_result.get("iou"), block_result.get("score")
        # the agent's own view of the case, from its trace
        events = run_dir / name / "events.jsonl"
        if events.is_file():
            builds, ended = 0, None
            for line in events.read_text().splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("t") == "step_start" and event.get("name", "").startswith(BUILD_STEPS):
                    builds += 1
                if event.get("t") == "case_end":
                    ended = event.get("status")
            row["builds"], row["agent_status"] = builds, ended
        return row

    rows = []
    # each comparison is its own process, so the pairs can be scored side by side
    with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 4) // 2)) as pool:
        for row in pool.map(score, names):
            rows.append(row)
            print(f"{row['case']:<14} {row['tier']:<8} score={_fmt(row['score'])} block={_fmt(row['block_score'])} | "
                  f"iou={_fmt(row['iou'])} surface={_fmt(row['surface'])} dims={_fmt(row['dims'])} "
                  f"holes={_fmt(row['holes'])} ({row['genus']}/{row['truth_genus']}) "
                  f"vol_err={_fmt(row['volume_err'])} {row['error']}", flush=True)
    return rows


def _fmt(value) -> str:
    return "  n/a" if value is None else f"{value:6.3f}"


def write_scores(run_dir: Path, rows: list[dict], meta: dict) -> None:
    fields = ["case", "tier", "written", "valid", "score", "block_score", "iou", "surface", "dims", "holes",
              "genus", "truth_genus", "block_iou", "bbox_err", "volume_err", "builds", "agent_status", "error"]
    with open(run_dir / "scores.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (run_dir / "scores.json").write_text(json.dumps({"meta": meta, "rows": rows}, indent=1, default=str))


def summarise(rows: list[dict]) -> str:
    def mean(group: list[dict], key: str) -> str:
        values = [r[key] for r in group if r.get(key) is not None]
        return f"{statistics.mean(values):.3f}" if values else "n/a"

    def line(label: str, group: list[dict]) -> str:
        scored = [r for r in group if r.get("score") is not None]
        written = sum(1 for r in group if r["written"])
        if not scored:
            return f"{label:<9} n={len(group):<3} written={written:<3} score: n/a"
        scores = [r["score"] for r in scored]
        paired = [r for r in scored if r.get("block_score") is not None]
        beats = sum(1 for r in paired if r["score"] > r["block_score"] + 1e-3)
        worse = sum(1 for r in paired if r["score"] < r["block_score"] - 1e-3)
        return (f"{label:<9} n={len(group):<3} written={written:<3} score mean={statistics.mean(scores):.3f} "
                f"median={statistics.median(scores):.3f} (iou {mean(group, 'iou')}, surface {mean(group, 'surface')}, "
                f"dims {mean(group, 'dims')}, holes {mean(group, 'holes')}) | envelope block score "
                f"{mean(group, 'block_score')}, iou {mean(group, 'block_iou')}; beats block: {beats}, "
                f"worse than block: {worse}")

    out = []
    for tier in ("simple", "medium", "complex"):
        group = [r for r in rows if r["tier"] == tier]
        if group:
            out.append(line(tier, group))
    out.append(line("all", rows))
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tier", nargs="*", choices=["simple", "medium", "complex"])
    parser.add_argument("--only", nargs="*", help="case names, e.g. test_case_12")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--engine", default=os.environ.get("AGENT_ENGINE") or "hull", choices=["hull", "graph", "legacy"])
    parser.add_argument("--no-llm", action="store_true", help="hull engine: deterministic stages only")
    parser.add_argument("--budget", type=float, help="AGENT_TIME_BUDGET_S for the whole run (default: 120 s per case)")
    parser.add_argument("--run-id", help="name of the run directory under runs/ (default: eval_<timestamp>)")
    parser.add_argument("--score-only", type=Path, help="existing run directory: skip the agent, just score")
    parser.add_argument("--pair", nargs=2, type=Path, help=argparse.SUPPRESS)   # internal: compare one pair
    args = parser.parse_args()

    if args.pair:
        try:
            print(json.dumps(compare_pair(*args.pair)))
        except Exception as exc:
            print(json.dumps({"valid": False, "error": f"{type(exc).__name__}: {exc}", **FAILED}))
        return 0

    index = read_index()
    if args.score_only:
        run_dir = args.score_only
        meta = {}
        if (run_dir / "scores.json").is_file():
            meta = json.loads((run_dir / "scores.json").read_text()).get("meta", {})
        names = meta.get("cases") or select_cases(index, args.tier, args.only, args.limit, args.offset)
        names = [n for n in names if (run_dir / n).is_dir() or (run_dir / "outputs" / f"{n}.stp").is_file()] or names
    else:
        names = select_cases(index, args.tier, args.only, args.limit, args.offset)
        if not names:
            print("no cases selected", file=sys.stderr)
            return 2
        run_dir = RUNS / (args.run_id or f"eval_{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        run_dir.mkdir(parents=True, exist_ok=True)
        budget = args.budget or 120.0 * len(names)
        print(f"run {run_dir.name}: {len(names)} cases, engine={args.engine}, budget={budget:.0f}s", flush=True)
        elapsed = run_agent(run_dir, names, args.engine, budget, {"AGENT_LLM_STAGE": "0"} if args.no_llm else {})
        meta = {"engine": args.engine, "cases": names, "tiers": args.tier, "elapsed_s": round(elapsed),
                "budget_s": budget}
        (run_dir / "scores.json").write_text(json.dumps({"meta": meta, "rows": []}))

    print(f"\nscoring {len(names)} cases against ground truth", flush=True)
    rows = score_run(run_dir, names, index)
    write_scores(run_dir, rows, meta)
    print(f"\n{summarise(rows)}\n\nscores: {run_dir / 'scores.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
