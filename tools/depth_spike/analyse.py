"""Spike, part 3: does a single-image depth model know the depths our solid gets wrong?

Per view the model's output is fitted to millimetres with one scale and one shift, in two ways:
`oracle` on the true depth (the most the model could give), `ours` on the depth of our own solid
with outliers left out (what the pipeline could do: most of our surface is right). Errors are in
per cent of the part's mean extent, on the pixels where our solid is right and where it is wrong
(more than WRONG per cent off).

    python tools/depth_spike/analyse.py <depth dir> <model tag> test_case_236 ...
"""

import sys
from pathlib import Path

import numpy as np

VIEWS = ("front", "back", "left", "right", "top", "bottom")
WRONG = 2.0   # per cent of the mean extent


def fit(x, y, robust: bool):
    """y ~ a x + b by least squares; `robust` drops the points furthest off and fits again, three times."""
    keep = np.ones(len(x), bool)
    for _ in range(4 if robust else 1):
        a, b = np.polyfit(x[keep], y[keep], 1)
        if not robust:
            break
        residual = np.abs(a * x + b - y)
        keep = residual <= max(np.percentile(residual, 70), 1e-6)
    return a, b


def main():
    folder, tag, names = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
    total = {k: [] for k in ("right_ours", "right_fit", "wrong_ours", "wrong_fit", "wrong_oracle", "closer", "wrong_share", "all_oracle")}
    print(f"{'case':>5} {'wrong px %':>10} | on wrong px: {'ours':>6} {'model':>6} {'oracle':>6} {'closer %':>8} | on right px: {'model':>6} | {'oracle all':>10}")
    for name in names:
        data = np.load(folder / f"{name}.npz")
        pred = np.load(folder / f"{name}_pred_{tag}.npz")
        size = float(np.mean(data["size"]))
        rows = {k: [] for k in total}
        for view in VIEWS:
            truth, ours, p = data[f"truth_{view}"], data[f"ours_{view}"], pred[view]
            mask = np.isfinite(truth) & np.isfinite(ours)
            if mask.sum() < 500:
                continue
            t, o, x = truth[mask], ours[mask], p[mask]
            wrong = np.abs(o - t) > WRONG / 100 * size
            a, b = fit(x, o, robust=True)
            fitted = a * x + b
            ao, bo = fit(x, t, robust=False)
            oracle = ao * x + bo
            pct = lambda values: 100 * float(np.mean(values)) / size
            rows["wrong_share"].append((mask.sum(), 100 * wrong.mean()))
            rows["all_oracle"].append((mask.sum(), pct(np.abs(oracle - t))))
            if (~wrong).sum():
                rows["right_ours"].append(((~wrong).sum(), pct(np.abs(o - t)[~wrong])))
                rows["right_fit"].append(((~wrong).sum(), pct(np.abs(fitted - t)[~wrong])))
            if wrong.sum() > 200:
                rows["wrong_ours"].append((wrong.sum(), pct(np.abs(o - t)[wrong])))
                rows["wrong_fit"].append((wrong.sum(), pct(np.abs(fitted - t)[wrong])))
                rows["wrong_oracle"].append((wrong.sum(), pct(np.abs(oracle - t)[wrong])))
                rows["closer"].append((wrong.sum(), 100 * float(np.mean(np.abs(fitted - t)[wrong] < np.abs(o - t)[wrong]))))
        def mean(key):
            items = rows[key]
            return sum(n * v for n, v in items) / sum(n for n, _ in items) if items else float("nan")
        for key in total:
            total[key] += rows[key]
        print(f"{name.split('_')[-1]:>5} {mean('wrong_share'):10.1f} |              {mean('wrong_ours'):6.1f} {mean('wrong_fit'):6.1f} {mean('wrong_oracle'):6.1f} {mean('closer'):8.0f} |              {mean('right_fit'):6.1f} | {mean('all_oracle'):10.1f}")
    def mean(key):
        items = total[key]
        return sum(n * v for n, v in items) / sum(n for n, _ in items) if items else float("nan")
    print(f"{'all':>5} {mean('wrong_share'):10.1f} |              {mean('wrong_ours'):6.1f} {mean('wrong_fit'):6.1f} {mean('wrong_oracle'):6.1f} {mean('closer'):8.0f} |              {mean('right_fit'):6.1f} | {mean('all_oracle'):10.1f}")


main()
