"""Does src/agentic_cad/regions.py call the areas of the straight views what they are?

    uv run python tools/check_regions.py                      # the 24 fixed eval cases, against the ground truth
    uv run python tools/check_regions.py --only test_case_5   # chosen eval cases
    uv run python tools/check_regions.py --practice           # the 9 practice parts: counts only, no truth
    uv run python tools/check_regions.py --save DIR           # also write every view with its regions coloured
    uv run python tools/check_regions.py -v                   # list every region that was called wrongly

What an area really is comes from the normals of the ground truth drawn from the same camera: flat if
nine tenths of its pixels look within 3 degrees of the same way, then "facing" if that way is within
3 degrees of the camera and "slanted" otherwise; "curved" if not flat. In the pictures facing is grey,
slanted blue, curved orange.
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
CACHE = ROOT / "runs" / "_shading_cache"   # our solid after steps 1 to 4, written by tools/check_shading.py
KINDS = ("facing", "slanted", "curved")
COLOURS = {"facing": (150, 150, 150), "slanted": (230, 140, 40), "curved": (40, 150, 240)}  # BGR
FLAT_DEG = 3.0
ANGULAR = 0.05


def true_kind(normals: np.ndarray) -> tuple[str, float, float]:
    """Kind, tilt of the mean normal from the camera (degrees), and the angle within which nine tenths
    of the normals lie around the mean (degrees). `normals` are in the camera's frame."""
    mean = normals.mean(axis=0)
    mean /= max(float(np.linalg.norm(mean)), 1e-9)
    around = np.degrees(np.arccos(np.clip(normals @ mean, -1, 1)))
    spread = float(np.percentile(around, 90))
    tilt = float(np.degrees(np.arccos(np.clip(mean[2], -1, 1))))
    if spread > FLAT_DEG:
        return "curved", tilt, spread
    return ("facing" if tilt <= FLAT_DEG else "slanted"), tilt, spread


def run(job: dict) -> dict:
    os.environ["TRACE"] = "0"
    import cv2

    import check_normals
    from agentic_cad import regions, verify
    from agentic_cad import visual_hull as vh

    name, practice = job["name"], job["practice"]
    try:
        case = (ROOT / "artifacts" / "inputs" if practice else EVAL / "inputs") / name
        dims = dict(zip("xyz", vh.read_dimensions(case)))
        observed = verify.observe(case, dims)
        found = regions.find(observed)
        # the second opinion from the isometric view needs a solid: ours after the earlier steps
        solved = {}
        cached = CACHE / f"{'practice_' if practice else ''}{name}.brep"
        if cached.is_file():
            from agentic_cad import normals, shading
            ours = vh.largest_solid(vh.read_brep(str(cached)))
            drawings = shading.Drawings(ours, observed, dims)
            for view, entries in normals.refine(found, observed, drawings, dims).items():
                for item, second, answer in entries:
                    solved[view, item.label] = (second, answer)
        mesh = None
        if not practice:
            mesh = verify.tessellate(check_normals.truth(name), 0.3 * min(o.mm_per_px for o in observed.values()), ANGULAR)
        rows = []
        for view, (labels, items) in found.items():
            seen = observed[view]
            if mesh is not None:
                drawing = verify.draw(mesh, view, dims, seen.mm_per_px, seen.mask.shape[0])
                frame = np.stack(verify.camera(view))
                clear = ~(cv2.dilate(drawing.edges.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
            for item in items:
                row = {"case": name, **item.summary()}
                second, answer = solved.get((view, item.label), (None, None))
                row["kind2"] = answer.kind if answer and answer.kind else item.kind   # after the isometric view had its say
                row["read"] = second is not None
                row["judged"] = answer is not None and answer.kind is not None
                row["compared"] = answer is not None
                if answer:
                    row["tilt2"], row["normal2"] = answer.tilt, [float(v) for v in answer.normal]
                    row["solid_off"] = float(np.degrees(np.arccos(np.clip(second.solid_normal @ answer.normal, -1, 1))))
                    row["solid_bend"] = second.solid_bend
                    row["misfit2"], row["facing_misfit2"] = answer.misfit, answer.facing_misfit
                    row["guess_misfit"] = answer.guess_misfit
                if mesh is not None:
                    pixels = (labels == item.label) & drawing.mask & clear
                    if pixels.sum() < 50:
                        continue  # the ground truth shows no face there to compare with
                    row["true"], row["tilt"], row["bend"] = true_kind(drawing.normal[pixels] @ frame.T)
                    if answer:   # how far the solved normal is from the true one
                        mean = drawing.normal[pixels].mean(axis=0)
                        mean /= max(float(np.linalg.norm(mean)), 1e-9)
                        row["off"] = float(np.degrees(np.arccos(np.clip(mean @ answer.normal, -1, 1))))
                rows.append(row)
            if job["save"]:
                picture = seen.image.copy()
                for item in items:
                    area = labels == item.label
                    picture[area] = (0.45 * picture[area] + 0.55 * np.array(COLOURS[item.kind])).astype(np.uint8)
                cv2.imwrite(str(Path(job["save"]) / f"{'practice_' if practice else ''}{name}_{view}.png"), picture)
        return {"name": name, "rows": rows}
    except Exception as error:
        import traceback
        return {"name": name, "error": repr(error), "trace": traceback.format_exc()}


def table(rows: list[dict], weight, key: str = "kind") -> None:
    rows = [dict(r, kind=r[key]) for r in rows]
    total = sum(weight(r) for r in rows) or 1
    print(f"{'really':>10} |" + "".join(f"{'called ' + k:>16}" for k in KINDS) + f"{'share':>8}")
    for true in KINDS:
        group = [r for r in rows if r["true"] == true]
        size = sum(weight(r) for r in group)
        cells = "".join(f"{sum(weight(r) for r in group if r['kind'] == k) / max(size, 1):>16.1%}" for k in KINDS)
        print(f"{true:>10} |{cells}{size / total:>8.1%}")
    right = sum(weight(r) for r in rows if r["kind"] == r["true"])
    print(f"{'right':>10} | {right / total:.1%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", nargs="*", metavar="test_case_<n>")
    parser.add_argument("--practice", action="store_true")
    parser.add_argument("--save", metavar="DIR")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    import eval_hull
    tiers = {}
    if args.practice:
        names = args.only or [f"test_case_{n}" for n in range(1, 10)]
    else:
        with open(EVAL / "index.csv", newline="") as f:
            tiers = {f"test_case_{row['n']}": row["tier"] for row in csv.DictReader(f)}
        names = args.only or eval_hull.sample(tiers, 8)
    if args.save:
        Path(args.save).mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(run, [{"name": n, "practice": args.practice, "save": args.save} for n in names]))
    rows = []
    for result in results:
        if "error" in result:
            print(f"{result['name']:14}FAILED {result['error']}\n{result.get('trace', '')}")
        else:
            rows += result["rows"]

    if args.practice:
        print(f"{'case':14}" + "".join(f"{k:>18}" for k in KINDS) + "   (regions, share of the area)")
        for name in names:
            mine = [r for r in rows if r["case"] == name]
            area = sum(r["area_px"] for r in mine) or 1
            flat = [r for r in mine if r["kind"] != "curved"]
            confirmed = [r for r in flat if r["judged"]]
            changed = sum(r["kind2"] != r["kind"] for r in confirmed)
            print(f"{name:14}" + "".join(
                f"{sum(r['kind'] == k for r in mine):>9}{sum(r['area_px'] for r in mine if r['kind'] == k) / area:>9.0%}"
                for k in KINDS) + f"   flat regions confirmed by the isometric view: {len(confirmed)} of {len(flat)} "
                f"({sum(r['area_px'] for r in confirmed) / max(1, sum(r['area_px'] for r in flat)):.0%} of their area), "
                f"{changed} change kind")
        return

    print(f"{len(rows)} regions of {len(names)} cases\n\nby number of regions:")
    table(rows, lambda r: 1)
    print("\nby area:")
    table(rows, lambda r: r["area_px"])
    print("\nright, by area, per tier: " + ", ".join(
        f"{tier} {sum(r['area_px'] for r in rows if tiers[r['case']] == tier and r['kind'] == r['true']) / max(1, sum(r['area_px'] for r in rows if tiers[r['case']] == tier)):.1%}"
        for tier in ("simple", "medium", "complex")))
    flat = [r for r in rows if r["kind"] != "curved"]
    judged = [r for r in flat if r["judged"]]
    print(f"\nwith the isometric view (solid from {CACHE.name}): of {len(flat)} flat regions "
          f"{sum(r['read'] for r in flat)} could be read there, {sum(r['compared'] for r in flat)} evenly, and for {len(judged)} the two tones confirm the solid's face "
          f"({sum(r['area_px'] for r in judged) / max(1, sum(r['area_px'] for r in flat)):.0%} of the flat area)")
    print("by area, after it:")
    table(rows, lambda r: r["area_px"], "kind2")
    print("by number of regions, after it:")
    table(rows, lambda r: 1, "kind2")
    for label, group in (("judged regions that are really facing", [r for r in judged if r["true"] == "facing"]),
                         ("judged regions that are really slanted", [r for r in judged if r["true"] == "slanted"])):
        if group:
            before = sum(r["kind"] == r["true"] for r in group)
            after = sum(r["kind2"] == r["true"] for r in group)
            print(f"  {label}: {len(group)}, right before {before}, after {after}")
    slanted = [r for r in judged if r["true"] == "slanted" and r["kind2"] == "slanted"]
    if slanted:
        print(f"  normal of the {len(slanted)} really slanted regions called slanted: off by "
              f"{np.median([r['off'] for r in slanted]):.0f} degrees (median), "
              f"{np.percentile([r['off'] for r in slanted], 90):.0f} (9 in 10)")
    if args.verbose:
        print("\nflat regions compared with the isometric view and not called facing: really, tilt, tilt of the normal given, "
              "that normal off the truth, off the solid's own normal, misfit of the solid's normal and of facing")
        for r in sorted((r for r in flat if r["compared"] and not (r["judged"] and r["kind2"] == "facing")), key=lambda r: -r["area_px"]):
            print(f"  {r['case']:14}{r['view']:8}{r['area_px']:>8} px  really {r['true']:8} tilt {r['tilt']:5.1f}  solved {r['tilt2']:4.0f}"
                  f"  off truth {r.get('off', float('nan')):4.0f}  off solid {r['solid_off']:4.0f} (bend {r['solid_bend']:3.0f})"
                  f"  misfit {r['misfit2']:5.1f} facing {r['facing_misfit2']:5.1f}  {'confirmed' if r['judged'] else 'not confirmed'}")
    wrong = sorted((r for r in rows if r["kind"] != r["true"]), key=lambda r: -r["area_px"])
    print("\nthe wrong ones, by what they are and what they were called (regions, median tilt, median bend in degrees):")
    for true in KINDS:
        for kind in KINDS:
            group = [r for r in wrong if r["true"] == true and r["kind"] == kind]
            if group:
                print(f"  {true:8} called {kind:8}{len(group):>5}   tilt {np.median([r['tilt'] for r in group]):5.1f}"
                      f"   bend {np.median([r['bend'] for r in group]):5.1f}   tone spread {np.median([r['spread'] for r in group]):4.0f}")
    if args.verbose:
        for r in wrong:
            print(f"  {r['case']:14}{r['view']:8}{r['area_px']:>8} px  really {r['true']:8} called {r['kind']:8}"
                  f" tone {r['tone']:>3} spread {r['spread']:>3}  tilt {r['tilt']:5.1f}  bend {r['bend']:5.1f}"
                  f"  then {r['kind2']:8}" + (f" tilt {r['tilt2']:4.0f} off {r.get('off', float('nan')):4.0f}" if r["judged"] else " (not confirmed)"))
        print("\nright before, wrong after the isometric view:")
        for r in sorted((r for r in rows if r["kind"] == r["true"] != r["kind2"]), key=lambda r: -r["area_px"]):
            print(f"  {r['case']:14}{r['view']:8}{r['area_px']:>8} px  really {r['true']:8} tone {r['tone']:>3}  then {r['kind2']:8} tilt {r['tilt2']:4.0f}")


if __name__ == "__main__":
    main()
