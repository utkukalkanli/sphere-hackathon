"""Which eval parts still hold far too much material after the hull and the holes?

    uv run python tools/find_hard.py                 # every eval part: hull and holes only, cached
    uv run python tools/find_hard.py --pick 20       # print the hard set from what is cached

The 24-part sample of tools/eval_hull.py is mostly easy, so a step that goes after large recesses
(a model's proposals, docs/plan-pipeline.md phases 3 and 4) needs its own parts: those whose solid
after hull and holes holds more than 1.5 times the true volume, without the cage-like parts that
fill under 5 % of their box (eval 51 and 99: a kind the practice set does not have).

This runs steps 1 and 2 on all 266 parts: minutes of every core. It is not the 266-part eval (no
later step runs), but ask before running it. Results are cached per part in runs/_eval_cache, so an
interrupted run goes on where it stopped.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "datasets" / "eval"
CACHE = ROOT / "runs" / "_eval_cache"
MIN_RATIO = 1.5    # our volume after the holes over the true one
MIN_FILL = 0.05    # the part's volume over its box


def measure(name: str) -> dict:
    os.environ["TRACE"] = "0"
    path = CACHE / f"hard_{name}.json"
    if path.is_file():
        return json.loads(path.read_text())
    from agentic_cad import visual_hull as vh
    from agentic_cad.hull_agent import VisualHullAgent

    case = EVAL / "inputs" / name
    views = sorted((case / "images").glob("view_*.png"))
    started = time.time()
    agent = VisualHullAgent()
    try:
        solid = agent.carve(case.joinpath("description.md").read_text(), views)
        x, y, z = vh.read_dimensions(case)
        true = vh.read_volume(case)
        row = {"name": name, "ratio": vh.volume(solid) / true, "fill": true / (x * y * z),
               "seconds": round(time.time() - started, 1)}
    except Exception as error:
        return {"name": name, "error": repr(error)}
    finally:
        agent.close()
    path.write_text(json.dumps(row))
    return row


def pick(rows: list[dict], tiers: dict[str, str], count: int) -> list[str]:
    """Evenly spaced by excess within each tier, the same ones every time."""
    hard = [r for r in rows if "error" not in r and r["ratio"] > MIN_RATIO and r["fill"] >= MIN_FILL]
    chosen = []
    for tier in ("simple", "medium", "complex"):
        group = sorted((r for r in hard if tiers[r["name"]] == tier), key=lambda r: -r["ratio"])
        share = max(1, round(count * len(group) / max(len(hard), 1)))
        step = max(1, len(group) // share)
        chosen += [r["name"] for r in group[::step][:share]]
    return sorted(chosen, key=lambda n: int(n.removeprefix("test_case_")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pick", type=int, metavar="N", help="only print a hard set of about N parts from the cache")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    with open(EVAL / "index.csv", newline="") as f:
        tiers = {f"test_case_{row['n']}": row["tier"] for row in csv.DictReader(f)}
    CACHE.mkdir(parents=True, exist_ok=True)
    if args.pick:
        rows = [json.loads(p.read_text()) for p in sorted(CACHE.glob("hard_test_case_*.json"))]
    else:
        started = time.time()
        with ProcessPoolExecutor(args.workers) as pool:
            rows = list(pool.map(measure, list(tiers)))
        print(f"{len(rows)} parts in {time.time() - started:.0f} s; {sum(1 for r in rows if 'error' in r)} failed")
    hard = [r for r in rows if "error" not in r and r["ratio"] > MIN_RATIO]
    thin = [r for r in hard if r["fill"] < MIN_FILL]
    print(f"{len(rows)} parts measured; {len(hard)} hold more than {MIN_RATIO} times the true volume after the holes, "
          f"{len(thin)} of them fill under {MIN_FILL:.0%} of their box")
    chosen = pick(rows, tiers, args.pick or 20)
    by_name = {r["name"]: r for r in rows}
    for name in chosen:
        print(f"  {name:16s} {tiers[name]:8s} {by_name[name]['ratio']:6.2f} times the true volume, fills {by_name[name]['fill']:.2f} of its box")
    print("HARD =", json.dumps(chosen))


if __name__ == "__main__":
    main()
