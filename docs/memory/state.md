# Project state

Last updated: 2026-11-12.

## Pipeline

Everything so far is deterministic. No model is called anywhere yet.

| step | file | status |
| --- | --- | --- |
| 1. Visual hull: a box with the `facts.json` dimensions, cut through wherever a view is white (front/back, right/left, top/bottom). Before cutting, each traced outline is rebuilt from straight lines and circular arcs (`outline_fit.py`). A fourth cut along the isometric views is implemented but off since 2026-11-11 | `src/agentic_cad/visual_hull.py`, `src/agentic_cad/outline_fit.py` | committed on `feat/visual-hull` |
| 2. Holes: every cylindrical hole listed in `facts.json` is cut as a cylinder; a thinner coaxial boss inside a bore is kept | `src/agentic_cad/holes.py` | committed on `feat/visual-hull`, measured (below) |
| Curved recesses around an axis (runs after step 3, before step 4): seen along a boss or bore axis of `facts.json`, a ring that is flat in our solid but whose tones change in the view is a curved face the side views could not show. If the solid has the start of that face next to the ring, it is fitted (line or circle, the radius snapped to a torus radius of `facts.json`) and carried on under the ring; if not, a cone of each half angle and a torus of each radius listed in `facts.json` are tried and the one the verifier rates highest is kept. A candidate is dropped if its tones, by the renderer's tone table, are more than 10 levels off the view or fit worse than the flat face, or if it would reach through the box (`HULL_SKIP=curved` switches the step off) | `src/agentic_cad/curved.py`, `tone.py`, `tone_tables.npz`, `regions.py` | uncommitted, hooked into `LATER_STEPS`, measured (below) |
| 3. Rounds: along every boss or bore axis of `facts.json`, where both side views show one strip of the same width centred on the axis, the square the silhouettes left is cut to the circle (revolved fitted outline). Only where `facts.json` lists a boss of that radius there, or the part is a disc seen along the axis and has only lathe faces | `src/agentic_cad/rounds.py` | committed on `feat/visual-hull`, measured (below) |
| 4. Level faces: in each straight view, an area between the dark lines with the flat tone of a face looking at the camera is one face at one depth. Where it covers several flat levels of our solid, the whole area is cut down to the lowest (if that level covers at least 5 % of the area) | `src/agentic_cad/depth.py` | committed on `feat/visual-hull`, measured (below) |
| Verifier (not a step): draws a candidate solid from the 8 cameras and compares silhouettes, face-boundary lines, volume and area with the inputs. Steps 3 and 4 keep a cut only if its score does not drop | `src/agentic_cad/verify.py`, `tools/check_verify.py` | committed on `feat/visual-hull`, measured (below) |
| 5. Depth from shading: in each straight view, an area with the tone of a face square to the camera, enclosed by lines, is slid down along the view direction and projected into the isometric view that sees that side. The depth is taken where the pixels it lands on show that side's tone and its outline lands on dark lines; the column above it is cut down to there. Only points certainly visible from the isometric camera count. Up to 3 passes over the six views. Slanted flat faces (one even tone that is not the facing tone) are handled too, but only where one straight edge borders a face square to the view whose depth the solid already has: the plane is hung on that edge and only its slope is searched (`HULL_SHADING_SLANTED=0` switches this off). A second search (`HULL_SHADING_DEEP=0` switches it off) takes floors the first cannot judge, by tone alone: regions of at least 10000 px, the depth where the largest share of the region lands on the right tone (the shallowest of equally good ones), all such floors lowered together and each kept only if what is then certainly visible shows the tone. A face whose present surface already agrees with the isometric view is never touched. Curved faces are not done | `src/agentic_cad/shading.py`, `tools/check_shading.py` | uncommitted, hooked into `LATER_STEPS`, measured (below) |

Unused inputs so far: the text of `description.md` (only its Envelope line is read, as a fallback),
`context.png`, the shading of slanted and curved faces (step 5 uses the tone of flat faces only), and
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

Step 5 (depth from shading) on the same 24 parts, `tools/eval_hull.py --iou`, 2026-11-11 late:
sampled IoU 0.854 to 0.856 (simple 0.994, medium 0.861 to 0.868, complex 0.706 to 0.707); volume
score 0.859 to 0.863. It cut on 6 of the 24 parts, none got worse; the best gained 5 points (part
38, a U-channel, 0.58 to 0.63). The gain is small because most of the remaining excess is not a
flat floor square to a view that the isometric camera certainly sees.

- Practice set (real renders), our volume / true volume: part 7 goes from 2.14 to 2.09, part 4
  from 1.072 to 1.071, the others do not change. Parts 5, 6 and 9 get no proposal at all.
- Run time of the step alone, 4 processes at once: under 1 s when the solid is within 2 % of the
  true volume (it is skipped), otherwise 3 to 40 s; 66 s on practice part 7 (budget 45 s per case,
  `HULL_SHADING_BUDGET`, plus the run budget via `agent.out_of_time()`).
- Slanted faces, added later the same evening: no change on the 24 eval parts (IoU 0.856, one small
  cut on part 3). Practice part 7 goes on from 2.09 to 2.07 (one face at 46 degrees, 7.4 cm3).
  Practice part 9, the V block, gets nothing: its slanted faces border no face of known depth.
- Where the remaining excess sits, measured against the ground truth on the same 24 parts before
  step 5 (excess under each kind of area of the six straight views, in units of the part's volume,
  summed over parts and views): 11.4 under areas of the facing tone, 1.9 under slanted flat areas
  (1.7 of it in part 99), 6.3 under curved areas, 8.1 under the lines themselves (7.3 in part 99).
  So on this set the loss is flat floors that step 5 does not reach, not slanted faces.
- Exact overlap by boolean with the ground truth (8 parts): step 5 removed no real material on
  parts 38, 110, 236, 74, 109, 212, 182; on part 75 its cut took 0.65 % of the part with it.
- Deep flat pockets, 2026-11-11 night (tone-only second search, border points on the middle of the
  lines, tones measured as described in `knowledge.md`): `tools/eval_hull.py --iou` on the 24 parts
  gives 0.861 sampled IoU (simple 0.994, medium 0.883, complex 0.707), volume score 0.867. Against
  the state before this work: part 110 0.79 to 0.86, part 236 0.70 to 0.73, part 38 0.63 to 0.64, no
  part lower. Whole pipeline, peer's baseline before step 5: 0.854.
- Practice set with the step as it is now, our volume / true volume before and after it: part 4
  1.072 to 1.027, part 5 1.228 to 1.224, part 6 3.78 to 3.73, part 7 2.14 to 2.08, part 9 1.89 to
  1.82; parts 1, 2, 3, 8 unchanged. There is no ground truth for these, so the cuts are unchecked;
  part 4 (the crankshaft) should be looked at in the dashboard.
- Run time of the step, 4 processes at once: up to 44 s on the eval sample, 51 s on practice part 7.
- The chosen depths are 3 to 5 px shallower than the true floor on most checked cuts (110, 236, 38),
  which is the safe side; a few small regions are 2 to 12 px too deep (171 bottom, 3 front).
Curved recesses (`curved.py`), 2026-11-12:

- Practice 2 (poppet valve): 1.105 to 1.018 times the true volume, verifier 0.835 to 0.917. The cut
  is the ring-shaped trough of the dish, R15.25 from `facts.json`, 950 mm3.
- The fixed 24 eval parts: the step cuts on none of them, so their scores do not move.
- The 37 eval parts with only lathe faces (planes, cylinders, cones, tori, spheres) and a listed
- **Practice part 4 (crankshaft) is written as an invalid solid since the rounds step** (found
  2026-11-12 by the session working on lines, confirmed here). The solid is valid in memory and
  after `vh.tidy`, but `BRepCheck_Analyzer` on the `.stp` read back says invalid. Hull and holes
  read back valid; rounds does not (also with every later step skipped). The other 8 practice
  outputs read back valid. No check in the pipeline reads the file back. The committed
  `artifacts/outputs` are steps 1 + 2 and valid. Not fixed.

  axis, steps 1 to 3 then this step alone, sampled IoU: it cut on 5 and none got worse. Part 249 (a
  funnel) 0.725 to 0.981 (volume 1.361 to 1.007); 263 0.876 to 0.883; 79, 81 and 113 gain under
  0.002 (chamfers). 12 of the 37 have more than 5 % excess after step 3; the other 11 are not
  recesses around an axis (tyre tread, a spring, plates).
- Practice set after the whole pipeline with this step (2026-11-12, another session running),
  our volume / true volume: 1.003, 1.018, 1.000, 1.027, 1.226, 3.728, 2.076, 1.006, 1.822 for parts
  1 to 9. Only part 2 is cut by this step (was 1.10); part 7 was checked with and without it (2.076
  both). Part 4 is now 2.75 MB with 436 faces and part 7 2.14 MB; where that growth comes from was
  not looked into.
- Cost of the step: 0.6 s on practice 7 (no curved ring around any axis in the views, so the solid
  is never drawn), 1.6 s on practice 2, 4 s on practice 5 (faces proposed and then dropped by the
  tone check, which draws the solid from all 8 cameras).
- A first version cut wrongly on practice 5: a cone of 5 degrees through the whole part around three
  round bulges of the back view, 1.224 to 1.17, and the verifier accepted it because the volume came
- `tools/check_normals.py --table FILE.npz` writes the tone table the agent reads. The two in
  `src/agentic_cad/tone_tables.npz` were made that way: `organisers` from the 9 practice parts
  (normals of our own solid), `eval` from the 24 eval parts (ground truth normals).
- `tools/check_normals.py`: measures how well the tone of a pixel is predicted by the normal under
  it (ground truth normals on the 24 eval parts, our solid's on the practice parts with
  `--practice`, which needs the cache of `tools/check_shading.py --practice`). 96 s for the 24 parts
  with 4 processes. Uncommitted. Results in `knowledge.md` ("The tone is one function of the
  normal"). Nothing in the pipeline uses a normal from tone yet.
- `src/agentic_cad/regions.py` and `tools/check_regions.py`: every area between the lines of a
  straight view is called facing, slanted or curved from its tones alone (no solid needed).
  Uncommitted, **not a step and not used by any step yet**. 98.0 % of the area right on the 24 eval
  parts; details and limits in `knowledge.md`. `--practice --save DIR` writes the practice views
  with the areas coloured (the only check there is for them: by eye).
  closer. The tone check and the box check were added for that; with them practice 5 gets no cut.
- `tools/eval_hull.py --iou` on the 24 parts gave 0.8757 volume score and 0.8736 sampled IoU in
  this run against 0.8672 and 0.8612 an hour earlier with the same result for this step (no cut).
  The difference is in the complex tier from the top/bottom cut on (0.6935 to 0.7219), which this
  work did not touch; another session was running at the same time, so a cut that timed out in one
  run may have finished in the other. Not looked into.

- The image has not been built or run with step 5.

Verifier on the same 24 parts, 2026-11-11: the ground truth scores 0.96 on average (0.91 to 0.99),
our steps 1 + 2 solid 0.71, the bounding box 0.24; the ground truth scores at least as high as our
solid on all 24.

## Known weaknesses

- Blind pockets are found by step 5 only when their floor is flat, in sight of an isometric
  camera, and either square to a view or slanted next to a face of known depth; floors smaller than
  10000 px that no line confirms are skipped; the rest, and inner cavities, are not found. This is still the
  largest loss, mostly on complex parts (eval 51, 99, 38, 236; practice 5, 6, 9).
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
