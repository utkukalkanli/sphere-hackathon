# How submissions are scored

As announced by the organisers on 2026-11-11 (copied from their scoring slide).

**Run time:** 1 h of run time is allowed for the nine parts. (The README template still says 20 minutes
wall clock; confirm with the organisers which limit applies to the code-freeze re-run.)

```
score = 0.30 · volume + 0.30 · surface + 0.20 · dimensions + 0.20 · holes
```

| weight | component | question it answers | detail |
| --- | --- | --- | --- |
| 30 % | Volume overlap | Is the overall form right? | |
| 30 % | Surface match | Is the surface in the right place, and how precisely? | checked at 0.5–10 % of part size |
| 20 % | Dimensions | Is it the right size? | 2 % off loses 10 %; 20 % off loses all |
| 20 % | Holes | Are all the through-holes there? | counted exactly from CAD topology |

- **Alignment first:** rotation and translation only. No scaling, no mirroring.
- **Same scorer for all teams:** shipped with the practice set, so local scores match the leaderboard.

## What this means for the pipeline

- 40 % of the score (dimensions + holes) is exact bookkeeping: the bounding box must match `facts.json`, and the
  number of through-holes must equal `through_holes_genus`. A missing or an extra through-hole costs the same.
- Volume overlap and surface match reward putting material in the right place, not matching the volume number.
- `scripts/eval.py` computes all four terms and the weighted score. The organisers' scorer is not in this
  repository, so the curves are our reading of the slide, chosen as the one that reproduces the deck's
  calibration table best (`tools/scorer_calibration`, 15 eval parts, 2026-11-12): surface is the smaller of
  the two shares of sampled surface points that lie within a threshold of the other surface, averaged over
  8 thresholds from 0.5 to 10 % of the mean bounding-box extent; dimensions is the worst extent on a line
  through (0 %, 1), (2 %, 0.9) and (20 %, 0); holes is 1 for the right count, else the smaller count over the
  larger. Medians, ours against the deck: 2 % too large 0.89 / 0.90, 5 % too long 0.87 / 0.88, 10 % too large
  0.60 / 0.62, convex hull 0.53 / 0.51, a different part 0.02 / 0.04, bounding box 0.47 / 0.32 (still too
  kind there). The pair is compared in the output's own frame, without the rotation alignment the organisers
  apply.
