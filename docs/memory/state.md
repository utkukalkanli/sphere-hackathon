# Project state

Last updated: 2026-11-11, about 22:00 CET.

## Pipeline

Everything so far is deterministic. No model is called anywhere yet.

| step | file | status |
| --- | --- | --- |
| 1. Visual hull: a box with the `facts.json` dimensions, cut through wherever a view is white (front/back, right/left, top/bottom). Before cutting, each traced outline is rebuilt from straight lines and circular arcs (`outline_fit.py`). A fourth cut along the isometric views is implemented but off since 2026-11-11 | `src/agentic_cad/visual_hull.py`, `src/agentic_cad/outline_fit.py` | committed on `feat/visual-hull` |
| 2. Holes: every cylindrical hole listed in `facts.json` is cut as a cylinder; a thinner coaxial boss inside a bore is kept | `src/agentic_cad/holes.py` | committed on `feat/visual-hull`, measured (below) |
| 3. Rounds: along every boss or bore axis of `facts.json`, where both side views show one strip of the same width centred on the axis, the square the silhouettes left is cut to the circle (revolved fitted outline). Only where `facts.json` lists a boss of that radius there, or the part is a disc seen along the axis and has only lathe faces | `src/agentic_cad/rounds.py` | committed on `feat/visual-hull`, measured (below) |
| 4. Level faces: in each straight view, an area between the dark lines with the flat tone of a face looking at the camera is one face at one depth. Where it covers several flat levels of our solid, the whole area is cut down to the lowest (if that level covers at least 5 % of the area) | `src/agentic_cad/depth.py` | committed on `feat/visual-hull`, measured (below) |
| Verifier (not a step): draws a candidate solid from the 8 cameras and compares silhouettes, face-boundary lines, volume and area with the inputs. Steps 3 and 4 keep a cut only if its score does not drop | `src/agentic_cad/verify.py`, `tools/check_verify.py` | committed on `feat/visual-hull`, measured (below) |
| Blind pockets, slanted and curved faces from the shading and the isometric views | `src/agentic_cad/shading.py` | another session is writing it |

Unused inputs so far: the text of `description.md` (only its Envelope line is read, as a fallback),
`context.png`, the shading inside the views (the lines are used by step 4 and the verifier), and
every `facts.json` field except the bounding box, the volume, the two cylinder lists and the face
types.

Guards around every cut (`hull_agent.py`):

- Each cut runs in a child process with a timeout: 30 s for a straight cut, 40 s for the isometric
  cut and for the holes (`HULL_CUT_TIMEOUT`, `HULL_ISO_CUT_TIMEOUT`, `HULL_HOLES_TIMEOUT`). On a
  timeout or a failed boolean the previous solid is kept.
- A cut that leaves less than 97 % of the true volume from `facts.json` is undone.
- After each straight cut the solid is projected back onto that view and compared with the silhouette
  (`vh.projection_error`, 3 px band, at most 0.2 % of the area off). A cut that fails any of these is
  repeated with the traced-pixel outline; if a direction needed that, the whole hull is cut again
  with that direction first (only while the case has used under 20 s). A final `check_views` step
  compares the solid with all three silhouettes and rebuilds with traced outlines if it disagrees.
- If cutting all holes at once is refused, they are retried one `facts.json` entry at a time.
- Steps 3 and 4 run through `VisualHullAgent.refine` (`LATER_STEPS`; `HULL_SKIP=rounds,depth` switches
  them off). Each keeps a cut only if the verifier's score does not drop by more than 0.003.
- The whole run has one time budget (`HULL_RUN_BUDGET`, 17 minutes, `scripts/main.py`): the time left
  is shared among the cases still to do; a case past its share skips its later steps, and with under
  5 s per case left the remaining cases get their bounding box.
- An exception in one view direction or in the hole step keeps the solid carved so far; an
  exception anywhere else in a case makes `scripts/main.py` write the bounding box for that case
  and go on (uncommitted).

## Git

- `main`: template, eval set and the first dashboard. It has no `artifacts/outputs`, so **nothing
  has been submitted yet**.
- `feat/visual-hull` (pushed): step 1, tracing, the redesigned dashboard, and the 9 practice outputs
  of step 1 (44 MB).
- `feat/visual-hull` also has step 2, `tools/eval_hull.py`, the isometric cut switched off, and the
  practice outputs of steps 1 + 2 without the isometric cut.
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

Steps 1 + 2 with fitted outlines, no isometric cut (the current pipeline), fixed 24-part eval
sample, 2026-11-11. Score after each step, sampled IoU against the ground truth, and faces per solid:

| | box | front/back | right/left | top/bottom | holes | IoU | faces |
| --- | --- | --- | --- | --- | --- | --- | --- |
| simple (8) | 0.471 | 0.533 | 0.879 | 0.956 | 0.956 | 0.960 | 24 |
| medium (8) | 0.511 | 0.622 | 0.805 | 0.839 | 0.842 | 0.838 | 33 |
| complex (8) | 0.398 | 0.436 | 0.470 | 0.694 | 0.720 | 0.705 | 639 |
| all (24) | 0.460 | 0.531 | 0.718 | 0.830 | 0.839 | 0.834 | 232 |

- Outline fitting, same sample: traced pixels score 0.832 with 1245 faces per solid (IoU 0.827);
  lines only 0.839 with 388 faces; lines and arcs 0.839 with 232 faces (IoU 0.834). The gain in score
  is mostly part 99, whose cut now finishes inside the timeout (0.11 to 0.26).
- Holes (measured with traced pixels and the isometric cut on): sampled IoU 0.834 with holes, 0.824
  without. Holes were cut on 14 of the 24 parts; none got worse, the best gained 17 points (part 171).
- Lowest: parts 51 (0.14) and 99 (0.26), thin-walled parts whose true volume is 2 to 4 % of the box.

Where the remaining excess material is (same sample, traced-pixel outlines, 1200 sampled points per
part, 2026-11-11):
97 % of the points that are inside our solid but outside the ground truth can be seen from outside
along +-X, +-Y or +-Z. Only 3 % are hidden from all six directions. If everything visible from an
axis direction were removed, the sampled IoU would go from 0.827 to 0.970 (complex: 0.688 to 0.914).
We cut almost nothing that belongs to the part (0.4 % of the true volume, in complex parts).

Practice set, steps 1 + 2 without the isometric cut, our volume / true volume: part 8 is 1.01,
parts 1, 2, 3, 4, 5 are between 1.07 and 1.25, part 9 is 1.89, part 7 is 2.20, part 6 is 3.81.

Run time and Docker, 2026-11-11, with fitted outlines and the projection check: the 9 practice parts
take 8 s alone on the laptop. The image builds for `linux/amd64` and runs as a non-root user with
read-only inputs in 36 s with tracing on (emulated on an ARM laptop). In the image all 9 parts cut
cleanly: no fallback, no retry, no warning. Against a native run on the ARM laptop every solid has
the same face count and the same volume to 7 digits or better. **The committed `artifacts/outputs`
are the files the image produced**.

Steps 3 and 4 on the fixed 24-part eval sample, sampled IoU against the ground truth, 2026-11-11
(the hull changed slightly since the table above, so compare within this table):

| | steps 1 + 2 | + rounds | + level faces |
| --- | --- | --- | --- |
| simple (8) | 0.960 | 0.994 | 0.994 |
| medium (8) | 0.838 | 0.841 | 0.861 |
| complex (8) | 0.705 | 0.705 | 0.706 |
| all (24) | 0.834 | 0.847 | 0.854 |

- No part got worse in either step and none was cut below the true volume. Level faces changed 5 of
  the 24 parts; the best gained 12 points (part 182, raised letters on a plate).
- Practice set with steps 1 to 4, our volume / true volume (before in brackets): part 1 is 1.00
  (1.07), part 2 is 1.10 (1.23), part 3 is 1.00 (1.09), part 4 is 1.07 (1.24), part 7 is 2.14
  (2.20); parts 5, 6, 8, 9 hardly change (1.23, 3.78, 1.01, 1.89). All 9 are one valid solid.
  `artifacts/outputs` still holds the outputs of steps 1 + 2; they have to be regenerated from the
  amd64 image before a submission.
- Run time: the 9 practice parts take 87 s alone on the laptop (8 s with steps 1 + 2). Each verifier
  check costs 1 to 4 s; step 4 took up to 21 s on one eval part with 4 processes running.

Verifier on the same 24 parts, 2026-11-11: the ground truth scores 0.96 on average (0.91 to 0.99),
our steps 1 + 2 solid 0.71, the bounding box 0.24; the ground truth scores at least as high as our
solid on all 24.

## Known weaknesses

- Blind pockets (recesses our solid does not reach anywhere) and inner cavities are not found by
  steps 1 to 4. This is the largest loss, mostly on complex parts.
- The verifier cannot see a wrong cut that leaves silhouettes and lines alone and brings the volume
  closer (eval part 123: a face pushed back 5 mm passed it; the 5 % rule in step 4 stopped that one).
- Outlines that are neither straight nor circular (splines, ellipses of slanted cylinders) stay
  polylines. Practice outputs are now 0.05 to 1.3 MB (4.4 MB in total, was 44 MB).
- The timeouts and the run budget make the output depend on how fast the machine is: a cut that
  finishes here may time out on the organisers' machine and give a different solid.
- Not yet submitted: the image and the outputs are ready (see Measurements), but nothing is on `main`.
- The dashboard section of README.md describes the first dashboard, not the redesigned one.

## Tools

- `tools/eval_hull.py`: scores a fixed sample of 24 eval parts (8 per tier). Results are cached in
  `runs/_eval_cache`, keyed by the source files, and the step 1 solid has its own cache so work on
  later steps does not recompute it. `--all` runs all 266.
- `ui/server.py`: three columns (inputs, our solid with a strip of the solid after each step,
  ground truth). Reads `runs/`.
- `datasets/eval`: 266 parts (100 simple, 100 medium, 66 complex) from Fusion 360 Gallery and ABC,
  built by `tools/dataset/`.
