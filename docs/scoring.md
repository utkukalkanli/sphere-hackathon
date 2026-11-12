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
- `scripts/eval.py` computes all four terms and the weighted score, but the organisers' scorer is not in this
  repository yet, so the curves are our reading of the slide: surface is an F-score of sampled surface points
  averaged over thresholds of 0.5, 1, 2, 5 and 10 % of the bounding-box diagonal; dimensions is a line through
  (0 %, 1), (2 %, 0.9) and (20 %, 0) per extent, averaged; holes loses one share of the truth's hole count per
  missing or extra hole. The pair is compared in the output's own frame, without the rotation alignment the
  organisers apply.
