# Project state

Last updated: 2026-11-11, about 19:35 CET.

## Pipeline

Everything so far is deterministic. No model is called anywhere yet.

| step | file | status |
| --- | --- | --- |
| 1. Visual hull: a box with the `facts.json` dimensions, cut through wherever a view is white (front/back, right/left, top/bottom, then the two isometric views as a fourth direction) | `src/agentic_cad/visual_hull.py` | committed on `feat/visual-hull` |
| 2. Holes: every cylindrical hole listed in `facts.json` is cut as a cylinder; a thinner coaxial boss inside a bore is kept | `src/agentic_cad/holes.py` | written, **not committed, not yet measured on the eval set** |
| 3. Anything that finds pockets and other inner cavities | none | not started |

Unused inputs so far: the text of `description.md` (only its Envelope line is read, as a fallback),
`context.png`, the shading and edge lines inside the views, and every `facts.json` field except the
bounding box, the volume and the two cylinder lists.

Guards around every cut (`hull_agent.py`):

- Each cut runs in a child process with a timeout: 30 s for a straight cut, 40 s for the isometric
  cut and for the holes (`HULL_CUT_TIMEOUT`, `HULL_ISO_CUT_TIMEOUT`, `HULL_HOLES_TIMEOUT`). On a
  timeout or a failed boolean the previous solid is kept.
- A cut that leaves less than 97 % of the true volume from `facts.json` is undone.
- If cutting all holes at once is refused, they are retried one `facts.json` entry at a time.
- An exception in one view direction or in the hole step keeps the solid carved so far; an
  exception anywhere else in a case makes `scripts/main.py` write the bounding box for that case
  and go on (uncommitted).

## Git

- `main`: template, eval set and the first dashboard. It has no `artifacts/outputs`, so **nothing
  has been submitted yet**.
- `feat/visual-hull` (pushed): step 1, tracing, the redesigned dashboard, and the 9 practice outputs
  of step 1 (44 MB).
- Uncommitted on `feat/visual-hull`: step 2 (`holes.py`, changes in `hull_agent.py` and
  `visual_hull.py`) and `tools/eval_hull.py`. Another session was working on these, and running
  the 24-part eval, when this file was written.
- GitHub Actions variables for team-d are set (`ECR_REPOSITORY_URI`, `S3_SUBMISSION_URI`,
  `AWS_REGION`), so a push to `main` submits.

## Measurements

Score used locally: `min(ours, true) / max(ours, true)` of the volumes. While our solid still
contains the whole part this equals the overlap (IoU); a 40-part check with sampled points against
the ground truth gave the same numbers (0.863 and 0.879 both ways).

Step 1 on the eval set, 259 of 266 parts (7 ran past the 240 s measurement limit), 2026-11-11:

| | 6 views | + isometric |
| --- | --- | --- |
| mean | 0.837 | 0.845 |
| median | 0.932 | 0.936 |
| simple | 0.874 | 0.882 |
| medium | 0.856 | 0.865 |
| complex | 0.747 | 0.754 |

- The isometric cut never cut below the true volume. It was skipped on 29 parts where the scale of
  the isometric views could not be confirmed.
- Time per part: the six straight cuts take 0.9 s (median) and 10 s at most. The isometric cut
  takes 3.7 s (median), 63 s at the 90th percentile and 224 s at most. These were measured with 8
  processes running at once.

Step 1 on the practice set, our volume / true volume (1 is perfect): parts 1, 2, 3, 4, 5, 8 are
between 1.07 and 1.26; part 6 is 3.66, part 7 is 2.54, part 9 is 2.68. Those three have inner
cavities that no silhouette shows.

Step 2: only one data point so far. On practice part 5 it cut 31 holes and the volume ratio went
from 1.259 to 1.228.

## Known weaknesses

- Inner cavities and pockets are not found by either step. This is the largest loss, mostly on
  complex parts.
- Round outlines come out as polygons traced from pixels, so output files are 1 to 7 MB with
  hundreds of planar faces.
- The timeouts make the output depend on how fast the machine is: a cut that finishes here may time
  out on the organisers' machine and give a different solid.
- The Docker image has never been built or run. The Dockerfile has the libraries OpenCascade needs,
  but that is untested.
- The dashboard section of README.md describes the first dashboard, not the redesigned one.

## Tools

- `tools/eval_hull.py`: scores a fixed sample of 24 eval parts (8 per tier). Results are cached in
  `runs/_eval_cache`, keyed by the source files, and the step 1 solid has its own cache so work on
  later steps does not recompute it. `--all` runs all 266.
- `ui/server.py`: three columns (inputs, our solid with a strip of the solid after each step,
  ground truth). Reads `runs/`.
- `datasets/eval`: 266 parts (100 simple, 100 medium, 66 complex) from Fusion 360 Gallery and ABC,
  built by `tools/dataset/`.
