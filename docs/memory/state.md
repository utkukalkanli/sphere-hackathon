# Project state

Last updated: 2026-11-12.

## Pipeline

Everything so far is deterministic. No model is called anywhere yet.

| step | file | status |
| --- | --- | --- |
| 1. Visual hull: a box with the `facts.json` dimensions, cut through wherever a view is white (front/back, right/left, top/bottom). Before cutting, each traced outline is rebuilt from straight lines and circular arcs (`outline_fit.py`). A fourth cut along the isometric views is implemented but off since 2026-11-11 | `src/agentic_cad/visual_hull.py`, `src/agentic_cad/outline_fit.py` | committed on `feat/visual-hull` |
| 2. Holes: every cylindrical hole listed in `facts.json` is cut as a cylinder; a thinner coaxial boss inside a bore is kept | `src/agentic_cad/holes.py` | committed on `feat/visual-hull`, measured (below) |
| 3. Rounds: along every boss or bore axis of `facts.json`, where both side views show one strip of the same width centred on the axis, the square the silhouettes left is cut to the circle (revolved fitted outline). Only where `facts.json` lists a boss of that radius there, or the part is a disc seen along the axis and has only lathe faces | `src/agentic_cad/rounds.py` | committed on `feat/visual-hull`, measured (below) |
| Curved recesses around an axis (runs after step 3, before step 4): seen along a boss or bore axis of `facts.json`, a ring that is flat in our solid but whose tones change in the view is a curved face the side views could not show. If the solid has the start of that face next to the ring, it is fitted (line or circle, the radius snapped to a torus radius of `facts.json`) and carried on under the ring; if not, a cone of each half angle and a torus of each radius listed in `facts.json` are tried and the one the verifier rates highest is kept. A candidate is dropped if its tones, by the renderer's tone table, are more than 10 levels off the view or fit worse than the flat face, or if it would reach through the box (`HULL_SKIP=curved` switches the step off) | `src/agentic_cad/curved.py`, `tone.py`, `tone_tables.npz`, `regions.py` | committed on `feat/visual-hull` (818d5f4), measured (below) |
| 4. Level faces: in each straight view, an area between the dark lines with the flat tone of a face looking at the camera is one face at one depth. Where it covers several flat levels of our solid, the whole area is cut down to the lowest (if that level covers at least 5 % of the area) | `src/agentic_cad/depth.py` | committed on `feat/visual-hull`, measured (below) |
| Verifier (not a step): draws a candidate solid from the 8 cameras and compares silhouettes, face-boundary lines, volume and area with the inputs. Steps 3 and 4 keep a cut only if its score does not drop | `src/agentic_cad/verify.py`, `tools/check_verify.py` | committed on `feat/visual-hull`, measured (below) |
| 5. Depth from shading: in each straight view, an area with the tone of a face square to the camera, enclosed by lines, is slid down along the view direction and projected into the isometric view that sees that side. The depth is taken where the pixels it lands on show that side's tone and its outline lands on dark lines; the column above it is cut down to there. Only points certainly visible from the isometric camera count. Up to 3 passes over the six views. Slanted flat faces (one even tone that is not the facing tone) are handled too, but only where one straight edge borders a face square to the view whose depth the solid already has: the plane is hung on that edge and only its slope is searched (`HULL_SHADING_SLANTED=0` switches this off). A second search (`HULL_SHADING_DEEP=0` switches it off) takes floors the first cannot judge, by tone alone: regions of at least 10000 px, the depth where the largest share of the region lands on the right tone (the shallowest of equally good ones), all such floors lowered together and each kept only if what is then certainly visible shows the tone. A face whose present surface already agrees with the isometric view is never touched. Curved faces are not done | `src/agentic_cad/shading.py`, `tools/check_shading.py` | uncommitted, hooked into `LATER_STEPS`, measured (below) |
| Pockets under our surface (between steps 5 and 6): a closed figure of lines that a straight view draws inside one flat face of ours, and that lie under that face (`lines.py`: "below"), is the outline of a pocket floor. The outline is slid down along the view direction; the depth is taken where at least half of it lands on lines of the isometric view and 0.25 more than at a usual depth. The column over the figure is cut down to there, kept only if the verifier does not rate the solid lower and the part's volume is left. `HULL_SKIP=pockets` switches it off | `src/agentic_cad/pockets.py` | committed on `feat/visual-hull` (8268262), hooked into `LATER_STEPS` after shading, measured (below) |
| 6. Split faces (moves no material): a line of a straight view that runs through the inside of one face of ours, and that the isometric view confirms to lie on our surface, is drawn onto that face as an edge. Pieces whose ends meet are joined into one figure first. Kept only if the volume is unchanged and the verifier does not rate it lower. Runs on the merged solid and stays last; the export then skips `tidy` (`agent.keep_faces`). `HULL_SKIP=split` switches it off | `src/agentic_cad/split.py`, `src/agentic_cad/lines.py`, `tools/check_lines.py` | committed on `feat/visual-hull` (8268262), hooked into `LATER_STEPS` last, measured (below) |

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
- The image has not been built or run with step 5.

Round sections on the evidence of a disc alone (`rounds.py`, evidence "disc", 2026-11-12,
uncommitted; `HULL_ROUNDS_DISC=0` switches it off): where the part is a disc seen along the axis but
has faces a lathe does not make, every stretch on which the two side views agree to 3 % of the
width is revolved from the wider view's outline, after the other rounds, and kept only if the
verifier's line term does not drop.

- Practice 6 (finned cone): 3.81 to 3.31 times the true volume in this step, line term 0.570 to
  0.759; whole pipeline 3.73 to 3.29. The step had left its cone square because of its spline faces.
- All 266 eval parts looked at for a proposal: 10 get one. Hull, holes and rounds with and without
  it, sampled IoU: 6 are cut and none is lower (31: 0.854 to 0.989; 261: 0.735 to 0.771; 130: 0.970
  to 0.991; 54, 6 and 42 gain 0.001 to 0.003), 3 are refused by the line term (144, 194, 252), 1 has
  nothing to cut. Mean of the 10: 0.870 to 0.890.
- What is left on practice 6, estimated from the front silhouette: as a body of revolution the
  part's outline holds 1235 cm3, 3.27 times the true 378 cm3. The flange alone (z 0 to 27.5) holds
  403 cm3, more than the whole part, and the surface area in `facts.json` is about twice that of
  the outside: the part is a shell a few mm thick, though the brief calls it solid. The grooves
  between the fins are the only outside excess left; everything else is the hollow, which no view
  shows (the bottom view is a closed face with one blind bore).

Level faces without a level in the solid, and slanted faces standing on them (`depth.py`,
2026-11-12; `HULL_DEPTH_SUPPORT=0` switches both off):

- What it does. A flat area with the facing tone under which the solid has no flat level is cut
  down to a level between the solid's lowest point over it and the level a twentieth of it lies
  under: the highest one at which a view at right angles shows a line (or a boundary between two
  of its areas) along at least 90 % of the face seen edge-on. Not where the solid's surface over
  the area is one plane. An area that lies along a straight edge of a face of known level is cut
  down to the steepest plane through that edge that stays inside the solid; an area along several
  such edges is solved as several planes that meet in a groove (the highest of them at each
  point), each of which must have one even tone. Both rest on the solid containing the part. Such
  cuts are kept if the verifier's score holds, or if they make 1 % more of the views' lines
  boundaries of the solid and leave the outline alone.
- 24 eval parts, the step with and without these cuts on the same solid (hull, holes, rounds,
  curved), sampled IoU, 2026-11-12 03:05 with the machine heavily loaded: mean 0.8668 without,
  0.8791 with. Part 38 (the U-channel whose floor no isometric camera sees) 0.628 to 0.933, volume
  1.623 to 1.107 times the truth; 235 0.995 to 0.997; 212 0.977 to 0.978; 1, 3 and 182 unchanged;
  27 0.984 to 0.982; **99 0.252 to 0.241** (six cuts on areas of 1200 to 4600 px took real
  material from its thin walls). The other 16 get no such cut.
- Practice 6: the step alone takes the round solid from 3.31 to 2.77 times the true volume. All 18
  flat triangles of the flange between the fins are levelled (edge-on line along 100 % of each)
  and most flanks are cut, at 82 to 83 degrees; two or three fins at the bottom of the top view are
  still one lump. The verifier's score does not move (0.2270).
- Practice set, whole pipeline as of commit a889456 plus this, 2026-11-12 03:14 (loaded machine),
  our volume / true volume for parts 1 to 9: 1.003, 1.018, 1.000, 1.028, 1.224, 2.759, 1.951,
  1.002, 1.754. All nine read back as one valid solid. Against the run of the evening before
  (1.003, 1.018, 1.000, 1.027, 1.226, 3.728, 2.076, 1.006, 1.822): part 6 is this work (rounds
  "disc" and these cuts); parts 7 and 9 also had the other sessions' new steps in between
  (pockets, isometric support), so their change is not split up here.
- Not done on practice 6: whether the top cap is round was not rechecked after the "disc" cut of
  its last 10 mm came back unchanged once; the lump of fins at the bottom of the top view.

Curved recesses (`curved.py`), 2026-11-12:

- Practice 2 (poppet valve): 1.105 to 1.018 times the true volume, verifier 0.835 to 0.917. The cut
  is the ring-shaped trough of the dish, R15.25 from `facts.json`, 950 mm3.
- The fixed 24 eval parts: the step cuts on none of them, so their scores do not move.
- The 37 eval parts with only lathe faces (planes, cylinders, cones, tori, spheres) and a listed
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
  closer. The tone check and the box check were added for that; with them practice 5 gets no cut.
- `tools/eval_hull.py --iou` on the 24 parts gave 0.8757 volume score and 0.8736 sampled IoU in
  this run against 0.8672 and 0.8612 an hour earlier with the same result for this step (no cut).
  The difference is in the complex tier from the top/bottom cut on (0.6935 to 0.7219), which this
  work did not touch; another session was running at the same time, so a cut that timed out in one
  run may have finished in the other. Not looked into.

Verifier on the same 24 parts, 2026-11-11: the ground truth scores 0.96 on average (0.91 to 0.99),
our steps 1 + 2 solid 0.71, the bounding box 0.24; the ground truth scores at least as high as our
solid on all 24.

Step 6 (split faces), 2026-11-12, solid after holes, rounds, level faces and shading (no "curved"):

- 24 eval parts: 13 already have more faces than the part and are skipped. Of the other 11 the step
  split faces on 3: part 27 from 202 to 206 faces (the part has 207), part 3 from 133 to 137 (240),
  part 123 from 144 to 145 (222). Volume unchanged, one valid solid, the split survives the `.stp`;
  the verifier's score rose a little each time (0.866 to 0.869 on part 27). So far it is a small
  step: most lines on our surface are open pieces of a figure whose other pieces the isometric
  camera does not see or that are too short, and such a figure splits nothing (eval 236).
- Practice set through `scripts/main.py`: faces split on 4 of 9 (part 3 from 8 to 9 faces, the
  part has 9; part 6 from 43 to 52 of 122; part 7 from 407 to 419 of 628; part 9 from 115 to 124
  of 371). Parts 1, 2, 4 already have more faces than the part; on 5 and 8 no figure split.
- Run time of the step: 4 to 19 s per practice part, 47 s on part 9 before the 15 s limit on the
  search (`HULL_SPLIT_BUDGET`) was added; not measured again since.
- `lines.py` against the ground truth (`tools/check_lines.py`, 1451 pieces, 63 385 px in the six
  straight views): pieces called "below" (the line lies under our surface) have excess material
  under them for 16 876 of 16 992 px; "level" is a pure split for 51 % of its length, most of the
  rest are rims of pockets we lack, which do lie on our surface. A third of the true pure splits
  are called "level"; 28 % of the length stays "unseen".
- Split after those changes, 24 eval parts (2026-11-12, on solids built while another session had
  an experimental outline tracing switched on, so the face counts themselves are not comparable):
  faces split on 13 parts instead of 3, between 1 and 8 new faces each, the verifier's line recall
  up on all 13 (eval 171: 0.682 to 0.726). On eval 181 and 73 the new faces are wrong: 12 to 15 and
  8 to 11 faces where the part has 7 and 8; the lines are drawn by our eval renderer and the truth
  has no edge there. Whether the real renders have such lines is not known.
- Pockets step, the same 24 parts: it cut on 2 and left 22 alone. Eval 236: four pockets of the back view, 0.58 to 0.68 mm deep, outline on
  isometric lines for 78 to 99 % against 13 to 26 % at a usual depth; volume 1.354 to 1.320 times
  the true one, sampled IoU 0.733 to 0.749. Eval 38: 1.608 to 1.597, IoU 0.639 to 0.645. No part
  lower. 0 to 12 s per part, 35 s on eval 3 (finding the lines). Not run on the practice set yet.
- Practice set through `scripts/main.py` with pockets and split in the pipeline (2026-11-12, run
  `20261112-012947-5078`, 6.5 minutes for the 9 parts with other sessions loading the laptop): all
  9 files read back as one valid solid, part 4 included. Pockets proposed nothing on any practice
  part. Split drew lines on parts 1 (18 to 19 faces, the part has 17), 3 (8 to 10; 9), 4 (454 to
  460; 178), 6 (43 to 52; 122), 7 (526 to 535; 628). Our volume / true volume: 1.003, 1.018, 1.000,
  1.027, 1.224, 3.728, 2.076, 1.006, 1.822.
- Cost of the two steps after that run: `lines.chains` looked every label up in the whole image
  (15 of 20 s on eval 3); done in one pass the finder takes 5 s there. When pockets cuts nothing it
  hands its lines to split (`agent.lines_found`). Practice 9: pockets 17 s and split 8 s, was 25 s
  and 21 s; practice 1: 0 s and 6.5 s.
- Where the lines under our surface go on the way to a pocket (24 eval parts, 2026-11-12, length
  of "below" pieces in px): 15 186 in all, on 7 parts (eval 3: 5681, 38: 2985, 236: 2324, 99:
  2207, 171: 1003, 51: 889, 27: 97); 3423 in a closed figure; 3330 of those mostly "below" and
  large enough; 820 over one flat level of our solid; 345 with a depth that stands out (eval 236).
- Split no longer draws the seam of a face of revolution (2026-11-12, `split.seams`). The wrong
  new faces on eval 181 and 73 were that: after the change neither is split (73 stays at 8 faces,
  the part has 8; 181 at 10 of 7, the excess is from elsewhere), and practice 3 goes from 8 to 9
  faces, which is what the part has (it went to 10: its bore is two half faces, two lines, of which
  one is now left out). Still split as before: eval 27 (206 to 210 of 207), 182, 171, 123,
  practice 1 (18 to 19 of 17; its line across the bottom is not a seam). Checked on these parts
  only, not on all 24 again.
- Practice part 4 read back as an invalid solid; traced to the rounds step and fixed (see Known
  weaknesses).
- 2026-11-12 later: the cap on the part's face count was removed (user: practice part 1 lacks the
  line across its bottom although the solid has more faces than the part; with the cap gone it is
  drawn, 22 to 23 faces). Pieces on one line or circle are merged and a figure may be closed by
  pieces the isometric view cannot see, if it confirms half of the figure's length. The numbers
  above are from before these three changes.

Shared datums (`src/agentic_cad/datums.py`, committed, **off by default** since 2026-11-12 (user),
`HULL_DATUMS=1` switches it on; hooked into `hull_agent._carve`), hull + holes only, 24 eval parts, 2026-11-12, off against on:
faces per solid 235 to 236 (without parts 51, 99 and 75: 51.9 to 49.9, thin ones 3.0 to 2.6, IoU 0.9035 to 0.9036), faces thinner than 2 px
(2 x area / perimeter under 0.2 % of the largest box side) 53.3 to 50.1 per solid, sampled IoU
0.8335 to 0.8445. All of the IoU gain is part 75 (0.675 to 0.974: its top/bottom cut goes through
instead of failing), and it is not robust: with `HULL_DATUMS_PX=1.0` part 75 is back at 0.675.
Part 51 (thin walls) drops 0.106 to 0.068. Fewer faces on 10 parts (best: 74, 43 to 34; 146, 83 to
71), more on none but 75 and 51. Practice set: parts 4 and 7 lose faces (204 to 185, 172 to 168),
part 8 gains 20 (133 to 153, not yet explained), volumes change under 0.2 %. Cost 0.1 to 0.2 s per
part. The whole pipeline (`tools/eval_hull.py --iou`) has not been run with it. The step is small
gain and some risk, so it is off.

Edge tracing (`HULL_TRACE=edges` in `visual_hull.py`, committed, **off by default**), hull + holes,
24 eval parts, 2026-11-12, centres against edges: sampled IoU 0.8335 to 0.8390, but only through
part 75 (0.675 to 0.913, its top/bottom cut goes through) and part 51 (0.106 to 0.125); 14 of the
other 22 parts get worse by 0.1 to 2.8 points (37: 0.990 to 0.983, 27: 0.984 to 0.974, 74: 0.847 to
0.819) and none better. Faces 235 to 194 (51: 1384 to 498, its fitted cut works). Practice volumes
rise by 0.02 to 2.3 points of the true volume. With the thin-wall guard on top (`HULL_THIN_GUARD=1`)
faces go to 445 per solid: it rejects loops at sharp tips and they come back as staircases.

Tight cuts and thin-walled silhouettes (`hull_agent._cut_direction`, `vh.ortho_silhouette`,
committed, on), hull + holes, 24 eval parts and the 9 practice parts, 2026-11-12, before against
after: sampled IoU 0.8335 to 0.8447, faces per solid 235 to 202, faces thinner than 2 px 53.3 to
36.0, hull time 4.6 to 3.7 s per part. Only two parts change: eval 75 (IoU 0.675 to 0.925, its
top/bottom cut is now kept at 0.956 of the true volume) and eval 51 (faces 1384 to 485, IoU 0.106 to
0.125, 62 to 11 s: its top/bottom silhouette is traced along pixel edges and the fitted cut works).
The other 22 eval parts and all 9 practice parts come out identical (faces, thin faces, volume).
Not yet run through the later steps (`tools/eval_hull.py --iou`). Eval 99 (3130 faces) is untouched.

Isometric support (`hull_agent._with_iso`, committed, on; rule in `decisions.md`), hull + holes,
same parts, 2026-11-12, before against after: sampled IoU 0.8447 to 0.8541, faces per solid 202 to
258, faces thinner than 2 px 36 to 65, hull time 3.7 to 4.8 s per part. It ran on the four eval
parts over 1.5 times the true volume and was kept on all four: 99 (IoU 0.250 to 0.406, faces 3130
to 3919, 39 to 60 s), 38 (0.576 to 0.628, 10 to 15), 171 (0.738 to 0.751, 110 to 190), 51 (0.125 to
0.128, 485 to 958, 11 to 30 s). The other 20 are identical. Practice, volume / true: part 6 3.81 to
3.62, part 7 2.20 to 2.09, part 9 1.89 to 1.88 (faces 28 to 46, 172 to 238, 98 to 170); the other
six identical. In the whole-pipeline run below it was kept on the same four parts.

Isometric support judged by the views (`src/agentic_cad/iso_support.py`, committed, on), hull +
holes, 24 eval parts and practice, 2026-11-12 about 03:00 (laptop heavily loaded, times unreliable):
at the 1.5 threshold the result is the same on all four parts (IoU 0.8541). With
`HULL_ISO_EXCESS=1.05` it also runs on 7 more eval parts: 181 0.828 to 0.885, 3 0.917 to 0.934, 182
0.871 to 0.875, none lower, mean 0.8574; but 74 and 123 get twice the faces for 0.1 to 0.2 % of the
volume, and practice 2 and 4 lose 3 and 5 % of the true volume before the rounds step has seen their
square sections. A minimum gain of 1 % of the true volume was added after that run
(`HULL_ISO_MIN_GAIN`). The 1.05 threshold has not been run through the later steps; the default
stays 1.5. Whether the piece-by-piece path ran on any part was not recorded in that run.

Whole pipeline on the fixed 24 eval parts, `tools/eval_hull.py --iou`, 2026-11-12 about 02:35,
working tree of all three sessions (commit 8268262 plus the uncommitted step 1 changes, the "disc"
rounds, the new `depth.py` with `MIN_EDGE_ON` 0.9, curved), 4 processes, nothing else multi-process
running, sources unchanged during the run. Volume score after each step, then sampled IoU and faces:

| | top_bottom | iso | holes | rounds | depth | shading | pockets | split | IoU | faces |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| simple (8) | 0.956 | 0.956 | 0.956 | 0.994 | 0.994 | 0.994 | 0.994 | 0.994 | 0.994 | 22 |
| medium (8) | 0.839 | 0.845 | 0.848 | 0.850 | 0.870 | 0.890 | 0.892 | 0.892 | 0.891 | 76 |
| complex (8) | 0.718 | 0.740 | 0.764 | 0.764 | 0.765 | 0.768 | 0.768 | 0.768 | 0.760 | 768 |
| all (24) | 0.838 | 0.847 | 0.856 | 0.869 | 0.876 | 0.884 | 0.885 | 0.885 | 0.882 | 289 |

- Against the last whole-pipeline run (0.861 sampled IoU, 0.867 volume score, before the isometric
  support, the new depth step, pockets and split): 0.882 and 0.885.
- Pockets cut on eval 236 only (1.354 to 1.320 times the true volume), 0.0008 on the mean. Split
  drew lines on 9 parts (3, 27, 37, 51, 74, 123, 145, 171, 182); it moves no material.
- Lowest sampled IoU: 51 (0.128), 99 (0.406), 38 (0.689), 236 (0.749), 171 (0.770). Eval 75 ends
  below the true volume (0.925 IoU; its tight top/bottom cut is kept on purpose).
- Faces against `facts.json`: within 5 on eval 27 (210 of 207), 73 (8 of 8), 211 (10 of 10), 212
  (39 of 35), 181, 235; far off on 99 (3946 of 320), 51 (1191 of 737), 75 (144 of 599), 145 (70 of
  11), 109 (24 of 8).
- Time: 406 s for the 24 parts with 4 processes; per part 7 to 50 s, eval 51 81 s, eval 99 129 s.
  The sum over the parts is 700 s, 29 s a part.

Figures that end on other lines (pockets and split, 2026-11-12 about 03:30), each step
alone on the solid before it, 24 eval parts, sampled IoU with 20 000 points:

- Pockets now also takes an open figure whose two ends stop on another line of the view and
  closes it straight across. Eval 236: the two large pockets that run out to the rim are cut,
  volume 1.354 to 1.254 times the true one (1.320 before this change), IoU 0.740 to 0.796. Eval
  171: 1.322 to 1.318, IoU 0.7471 to 0.7472. The other 22 parts are not touched; none is lower.
- Split draws the figures of one face one after the other, each onto what the earlier ones made
  of the face, so a figure that ends on another one splits too (on a box: a T gives 8 faces, two
  rectangles sharing a side 8). On the 24 parts it splits the same 10 as before (123, 145, 171,
  182, 27, 3, 37, 51, 74 and one more face on 182); no part gained a split from it, 181 and 73
  stay unsplit. Every split solid is one valid solid with the volume unchanged.

- Practice set with these changes, whole pipeline through `scripts/main.py` (run
  `20261112-064134-d0e0`, 7.5 minutes for the 9 parts, working tree of all sessions): all 9 files
  read back as one valid solid. Pockets now cuts on three practice parts, where it proposed
  nothing before: part 5 (4 pockets, 0.02 % of the true volume), part 7 (12 of 13, 0.8 %), part 9
  (3, 2.5 %). There is no ground truth for them: on part 9 they are slivers beside a bore and
  along a slanted bank, closed straight across, and nobody has checked them. Faces after split:
  19 of 17, 22 of 12, 9 of 9, 542 of 178, 208 of 172, 799 of 122, 731 of 628, 275 of 306, 264 of
  371. Our volume / true volume: 1.003, 1.018, 1.000, 1.028, 1.224, 2.759, 1.936, 1.002, 1.729.
  Slowest cases: part 7 127 s, part 9 99 s, part 6 77 s.

## Known weaknesses

- **A solid can be valid in memory and invalid in the file.** Practice part 4 (crankshaft) was
  (found 2026-11-12): the rounds step revolved a fitted line 1.3 degrees off square to the axis
  into a cone of half angle 88.66 degrees, which `BRepCheck_Analyzer` accepts in memory and rejects
  after the `.stp` is read back. Fixed the same day: `export.reads_back_valid` writes and reads the
  file; `rounds.cut_rounds` uses it and cuts the axis lines one at a time when all together fail
  (part 4 keeps all but one: volume 1.049 times the true one instead of 1.027, 46 s for the case),
  and the final export falls back to the solid before the later steps if the last one fails it.
  The other steps do not ask it yet. The cause itself (the line that should be square) is with the
  session working on outlines.

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
- `ui/server.py`: one large 3D view of the solid after the chosen step, with the material that step
  removed drawn over it in translucent blue; a slider of the steps underneath (volume / true volume
  and one line on what the step does under each); the input is a small window laid over the bottom left corner of the big view, which now runs to
  the left edge (user, 2026-11-12: it should not take the whole left side): the view shown large,
  the others as a strip to switch between; the brief and the facts fold away under it, closed by default; user, 2026-11-12: the
  text was not read); right the ground truth filling its panel (the practice set has none: there the organisers'
  render of the part, never our output). Under the truth view two panels that fold away, never laid
  over a model (user): "Extracted" (the chosen
  step's attached images and `.json` lists, log, metrics) and "Diff" (the chosen step's solid against
  the truth: volume score, size per axis, volume, surface, faces, and where a ground-truth solid
  exists the sampled IoU, excess and missing volume, computed in the browser). Black background; areas are told apart by their surface, not by rules: no lines between bar, views,
  panels and slider, no frames inside a panel (user, 2026-11-12: too many lines and nested boxes).
  A diff mode of the big view (D, eval set only) paints our solid red where there is material under
  a face that the part does not have, and shows the truth in violet where we lack it. A face toggle (C)
  gives every face of the solids its own colour. A section plane (X cycles X, Y, Z, off; a slider
  places it) cuts both views open with the cut face filled in; with the diff on, the cut face is red
  where only our solid has material and violet where only the truth has. The case list
  stays in order of number (user: worst first made a number hard to find). A scores panel (S, or the
  button beside the list) opens over the input column: mean score of the set and per tier, then every
  scored case worst first with the score after each step as small bars; a click goes to the case.
  The score is the result of `tools/eval_hull.py` in `runs/_eval_cache` (sampled IoU if it was run
  with `--iou`), else the volume score of the case's newest traced run; the panel reads
  them again every 5 s while open. The case and run lists of the bar are our own component (user):
  typing a number or a name filters, each case row shows its score. The slider shows every step of
  the case from the start (2026-11-12, user): the agent writes its plan as the first trace event
  (`trace.plan`, `VisualHullAgent.planned`), and a step that has not run is drawn dimmed as pending,
  off (`HULL_SKIP`, isometric cut), skipped (out of time) or not run; the running one pulses. Runs
  traced before this have no plan and show only the steps that ran. Not yet looked at in a browser. The dark
  lines on a solid are the outlines of its B-rep faces (also between faces that meet smoothly), not
  edges found by angle; cut faces are hatched and outlined. Both side panels are resized by dragging their
  edge. Reads `runs/`. Committed on `feat/visual-hull`.
- `tools/check_normals.py --table FILE.npz` writes the tone table the agent reads. The two in
  `src/agentic_cad/tone_tables.npz` were made that way: `organisers` from the 9 practice parts
  (normals of our own solid), `eval` from the 24 eval parts (ground truth normals).
- `tools/check_normals.py`: measures how well the tone of a pixel is predicted by the normal under
  it (ground truth normals on the 24 eval parts, our solid's on the practice parts with
  `--practice`, which needs the cache of `tools/check_shading.py --practice`). 96 s for the 24 parts
  with 4 processes. Committed. Results in `knowledge.md` ("The tone is one function of the
  normal"). Nothing in the pipeline uses a normal from tone yet.
- `src/agentic_cad/regions.py` and `tools/check_regions.py`: every area between the lines of a
  straight view is called facing, slanted or curved from its tones alone (no solid needed).
  Committed; used by `curved.py`. 98.0 % of the area right on the 24 eval
  parts; details and limits in `knowledge.md`. `--practice --save DIR` writes the practice views
  with the areas coloured (the only check there is for them: by eye).
- `tools/check_lines.py`: grades `lines.py` against the ground truth on the 24 eval parts (what
  the truth has under each piece, and whether our surface is in front of the true one). Caches the
  pipeline's solid per part in `runs/_lines_cache`; about 3 minutes with 3 processes once cached.
  `--practice` gives counts only, `--save DIR` the views with the pieces drawn. Uncommitted.
- `src/agentic_cad/normals.py`: for a flat area of a straight view, whether the tones in that view
  and in the isometric view confirm the face the solid has there (facing or slanted, with its
  normal). `tools/check_regions.py` reports it next to the tone-only kinds; it reads our solid from
  `runs/_shading_cache`. Uncommitted, not a step and not used by any step: tried as a guard in the
  shading step and left out (`decisions.md`). Numbers in `knowledge.md`.
- `datasets/eval`: 266 parts (100 simple, 100 medium, 66 complex) from Fusion 360 Gallery and ABC,
  built by `tools/dataset/`.
