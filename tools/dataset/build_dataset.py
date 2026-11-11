"""Turn raw STEP datasets into extra test cases in the Sphere Forge input format.

  python -I build_dataset.py scan  --per-source 1500   # measure a random sample (resumable)
  python -I build_dataset.py build --per-tier 100      # balanced pick -> render + facts + brief

  datasets/raw/<source>/extracted/...       input STEP files
  datasets/scan/<source>.jsonl              cached measurements
  datasets/eval/inputs/test_case_<n>/       same layout as artifacts/inputs (no context.png)
  datasets/eval/ground_truth/test_case_<n>.stp
  datasets/eval/index.csv

Run the agent on it with:
  python scripts/main.py --input_dir datasets/eval/inputs --output_dir <somewhere>
"""
import argparse
import csv
import json
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "datasets"
RAW, SCAN, EVAL = DATA / "raw", DATA / "scan", DATA / "eval"
WORKER = Path(__file__).resolve().parent / "part_worker.py"
MAX_STEP_BYTES = 12 * 1024 * 1024
TIERS = ("simple", "medium", "complex")
SOURCES = ("fusion360_reconstruction", "fusion360_assembly", "abc")


def candidates(source: str):
    """Yield (source_id, path, name) for every STEP file of a source."""
    base = RAW / source / "extracted"
    if source == "fusion360_reconstruction":
        for p in sorted(base.rglob("*.step")):
            if len(p.stem.split("_")) == 3:  # <project>_<file>_<component>; longer stems are timeline intermediates
                yield p.stem, p, None
    elif source == "fusion360_assembly":
        names = {}
        for p in sorted(base.rglob("*.step")):
            if p.parent not in names:
                try:
                    bodies = json.loads((p.parent / "assembly.json").read_text()).get("bodies", {})
                    names[p.parent] = {k: v.get("name") for k, v in bodies.items()}
                except Exception:  # noqa: BLE001
                    names[p.parent] = {}
            name = names[p.parent].get(p.stem)
            if name and re.fullmatch(r"(Body|Solid|Component)\s*\d*(\s*\(\d+\))?", name.strip()):
                name = None
            yield f"{p.parent.name}_{p.stem[:8]}", p, name
    elif source == "abc":
        for p in sorted(base.rglob("*.step")):
            yield p.parent.name, p, None


def run_worker(args, timeout):
    try:
        r = subprocess.run([sys.executable, "-I", str(WORKER), *map(str, args)],
                           capture_output=True, text=True, timeout=timeout)
        lines = [ln for ln in r.stdout.strip().splitlines() if ln.startswith("{")]
        return json.loads(lines[-1]) if lines else {"ok": False, "reason": f"crash rc={r.returncode}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout"}


def cmd_scan(a):
    SCAN.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    for source in a.sources:
        out = SCAN / f"{source}.jsonl"
        done = {json.loads(ln)["id"] for ln in out.read_text().splitlines() if ln.strip()} if out.exists() else set()
        cands = [c for c in candidates(source) if c[1].stat().st_size <= MAX_STEP_BYTES]
        rng.shuffle(cands)
        todo = [c for c in cands[: a.per_source] if c[0] not in done]
        print(f"[{source}] {len(cands)} candidates, {len(done)} cached, scanning {len(todo)}", flush=True)
        with ThreadPoolExecutor(a.jobs) as ex, out.open("a") as fh:
            futs = {ex.submit(run_worker, ["scan", p], 90): (sid, p, name) for sid, p, name in todo}
            for i, f in enumerate(as_completed(futs), 1):
                sid, p, name = futs[f]
                res = f.result()
                keep = {k: res.get(k) for k in ("ok", "reason", "tier", "faces", "through_holes_genus",
                                                "bounding_box_mm", "volume_mm3", "_n_shells") if k in res}
                fh.write(json.dumps({"id": sid, "path": str(p.relative_to(RAW)), "name": name,
                                     "source": source, **keep}) + "\n")
                fh.flush()
                if i % 100 == 0:
                    print(f"  {i}/{len(todo)}", flush=True)


def usable(r):
    if not r.get("ok") or r.get("through_holes_genus") is None or r.get("_n_shells") != 1:
        return False
    env = list(r["bounding_box_mm"].values())
    if not (7 <= r["faces"] <= 1500):  # skip bare boxes/cylinders and monsters
        return False
    if max(env) < 5 or max(env) > 3000 or min(env) < 0.2:
        return False
    return 0.01 < r["volume_mm3"] / (env[0] * env[1] * env[2]) <= 1.0001


def cmd_build(a):
    rng = random.Random(a.seed)
    pool, seen = {t: {} for t in TIERS}, set()
    for source in a.sources:
        f = SCAN / f"{source}.jsonl"
        for ln in f.read_text().splitlines() if f.exists() else []:
            r = json.loads(ln)
            if not usable(r):
                continue
            sig = (r["faces"], round(r["volume_mm3"]), tuple(sorted(r["bounding_box_mm"].values())))
            if sig not in seen:
                seen.add(sig)
                pool[r["tier"]].setdefault(source, []).append(r)
    chosen = []
    for tier in TIERS:
        by_src = pool[tier]
        for lst in by_src.values():
            rng.shuffle(lst)
        picked = []
        while len(picked) < a.per_tier and any(by_src.values()):  # round-robin over sources
            for src in list(by_src):
                if by_src[src] and len(picked) < a.per_tier:
                    picked.append(by_src[src].pop())
        print(f"[{tier}] picked {len(picked)}: " +
              ", ".join(f"{s}={sum(p['source'] == s for p in picked)}" for s in a.sources), flush=True)
        chosen += picked

    # interleave tiers so that any prefix test_case_1..k is tier-balanced
    per = {t: [r for r in chosen if r["tier"] == t] for t in TIERS}
    order = [per[t][i] for i in range(max(map(len, per.values()))) for t in TIERS if i < len(per[t])]

    def job(n, r):
        case = EVAL / "inputs" / f"test_case_{n}"
        if (case / "facts.json").exists() and len(list((case / "images").glob("*.png"))) == 8:
            return n, r, {"ok": True, **json.loads((case / "facts.json").read_text())}
        return n, r, run_worker(["build", RAW / r["path"], EVAL, n, r["source"], r["id"], r.get("name") or ""], 300)

    rows = []
    with ThreadPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(job, n, r) for n, r in enumerate(order, 1)]
        for i, f in enumerate(as_completed(futs), 1):
            n, r, res = f.result()
            if res.get("ok"):
                bb = res["bounding_box_mm"]
                rows.append([n, r["tier"], r["source"], r["id"], res["faces"], res["through_holes_genus"],
                             bb["x"], bb["y"], bb["z"], round(res["volume_mm3"] / 1000, 3), r.get("name") or ""])
            else:
                print(f"  FAILED test_case_{n} ({r['source']} {r['id']}): {res.get('reason')}", flush=True)
            if i % 25 == 0:
                print(f"  {i}/{len(order)}", flush=True)
    rows.sort()
    with (EVAL / "index.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["n", "tier", "source", "source_id", "faces", "through_holes", "x_mm", "y_mm", "z_mm",
                    "volume_cm3", "name"])
        w.writerows(rows)
    print(f"wrote {len(rows)} test cases -> {EVAL}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "build"])
    ap.add_argument("--sources", nargs="+", default=list(SOURCES))
    ap.add_argument("--per-source", type=int, default=1500)
    ap.add_argument("--per-tier", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    {"scan": cmd_scan, "build": cmd_build}[a.cmd](a)
