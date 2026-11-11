# Project state

Last updated: 2026-11-12, about 00:30 CET (step 3: region depths, circles, shading).

## Pipeline

Steps 1 to 3 are deterministic. Step 4 (on `ananas` only) calls a model and runs only when
`OPENAI_MODEL` is set. The committed practice outputs are from steps 1 + 2 and are now out of date:
regenerate them in the amd64 image before a submission. A separate model-based agent exists on `feat/llm-cad-agent` (see Git).

| step | file | status |
| --- | --- | --- |
| 1. Visual hull: a box with the `facts.json` dimensions, cut through wherever a view is white (front/back, right/left, top/bottom). Before cutting, each traced outline is rebuilt from straight lines and circular arcs (`outline_fit.py`). A fourth cut along the isometric views is implemented but off since 2026-11-11 | `src/agentic_cad/visual_hull.py`, `src/agentic_cad/outline_fit.py` | committed on `feat/visual-hull` |
| 2. Holes: every cylindrical hole listed in `facts.json` is cut as a cylinder; a thinner coaxial boss inside a bore is kept | `src/agentic_cad/holes.py` | committed on `feat/visual-hull`, measured (below) |
| 3. Line cuts: in each straight view, regions enclosed by the render's dark lines are tried when their boundary has lines our solid lacks or their shading shows a cylinder. Depths come from the side-view lines that run across the region's own extent (falling back to any line across). A filled circle is tried as a cylinder standing towards the viewer: the square block around it is rounded down to a base depth. A region shaded like a cylinder (tone constant along one image axis) is also tried as a concave or convex cylinder, with diameters from `facts.json`. Kept only if the volume stays at or above the true volume, no view opens background, the line mismatch of all 8 views drops by 1 % and that of the two iso views drops too. 40 tries per case, at most 10 per region (`LINE_MAX_TRIES`), 90 s hard stop | `src/agentic_cad/edges.py`, `src/agentic_cad/line_cuts.py`, `hull_agent.cut_lines` | on `ananas`, measured on the practice set (below) |
| 4. Model-planned cuts: the brief, `facts.json` and the drilled holes go to an OpenAI-compatible model, which answers with box, cylinder and prism cuts as numbers. All are tried at once, then one by one; a cut is kept only if the solid stays at 97 % of the true volume or more and no view (6 straight + 2 iso, `views.py`) shows more than a few pixels of new background where the render shows the part. 75 s per case in all (`PLANNED_BUDGET`), 20 s per boolean, 60 s per request | `src/agentic_cad/features.py`, `src/agentic_cad/llm_cuts.py`, `hull_agent.cut_planned` | on `ananas`, measured with Qwen3-30B (below) |

Unused inputs without a model: the text of `description.md` (only its Envelope line is read, as a fallback),
`context.png`, the shading inside the views, and every `facts.json` field except the bounding box,
the volume and the two cylinder lists.

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
- An exception in one view direction or in the hole step keeps the solid carved so far; an
  exception anywhere else in a case makes `scripts/main.py` write the bounding box for that case
  and go on.

## Git

- `main`: template, eval set and the first dashboard. It has no `artifacts/outputs`, so **nothing
  has been submitted yet**.
- `feat/visual-hull` (pushed): steps 1 + 2, tracing, the redesigned dashboard, `tools/eval_hull.py`,
  the isometric cut switched off, and the practice outputs of steps 1 + 2 made by the amd64 image.
- `ananas` (the working branch since 2026-11-11 evening): all of `feat/visual-hull` (merged up to
  2bc65c8 in 679fbb3, not pushed yet), plus `views.py`, `tests/test_views.py`, `tools/iso_check.py`
  and the Windows dashboard fix, plus step 3. Step 3 is the useful part of 9364253, which the merge
  a8d1c91 had dropped because of conflicts: `features.py` and `llm_cuts.py` are back, as a step after
  the holes. Not taken over from 9364253: its own hole cutting (`holes.py` is measured and keeps
  bosses) and the view check on holes (it blocked practice 8's real through holes).
- `feat/llm-cad-agent` (pushed, a teammate's, not merged anywhere): a different approach that
  replaces `scripts/main.py`. A model writes a CadQuery script per case, it is run, checked against
  `facts.json` (box, volume, area, cylinder diameters) and the view silhouettes (IoU), and the
  differences go back to the model for up to 6 rounds, cases in parallel under a 17 min budget.
  Configured by `OPENAI_*` and `AGENT_*` variables; tested only with a scripted model, never against
  a real endpoint. Also has `scripts/serve_models.sh` (vLLM on the Brev GPU box: Qwen3-30B-A3B-FP8
  as LLM on port 8000, Qwen2.5-VL-7B as VLM on 8001), `docs/remote-dev.md`, and `cad/`: hand-written
  CadQuery models of the 9 practice parts with their STEP files in `artifacts/outputs` (volume
  within 0.5 % of `facts.json`). **Those outputs do not come from the pipeline**: merging that branch
  to `main` as it is would submit files the image cannot reproduce.
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

Step 3 (line cuts) on the practice set, our volume / true volume, Windows PC, 2026-11-12 (the eval
set is no longer used, see decisions.md). Score = mean of min/max over the 9 parts:

| version | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| steps 1 + 2 | 1.066 | 1.232 | 1.085 | 1.242 | 1.230 | 3.807 | 2.201 | 1.008 | 1.891 | 0.725 |
| lines only (733b9f6) | 1.002 | 1.118 | 1.001 | 1.163 | 1.013 | 3.318 | 2.195 | 1.008 | 1.891 | 0.780 |
| + 10 tries per region | 1.002 | 1.120 | 1.001 | 1.163 | 1.013 | 3.318 | 2.195 | 1.008 | 1.724 | 0.785 |
| + region depths, circles (current) | 1.002 | 1.120 | 1.001 | 1.130 | 1.009 | 3.318 | 2.142 | 1.008 | 1.403 | 0.804 |

- Run time of the current version: 10 to 73 s per part, about 5.5 minutes for all 9.
- Circles: kept on parts 5 (two of the six d 51 bores' bosses, base 12.8 mm deep) and 7.
- Shading: no cylinder cut was ever kept. Putting shaded regions first used up the tries and made
  parts 2 to 5 worse (score below 0.78); now they are tried in the normal order, cylinders before
  flat pockets. Flat faces with a light gradient are taken for cylinders too (practice 3's flange).
- Part 1 lands on the true volume (the rib's arcs cut 5 mm deep from the back), part 3 too (the
  flange step, 19 mm from the left).
- Part 5 gets 63 mm pockets from the back inside the large circles; the brief says they open into
  the hollow body, so probably right, unverified. Before the floor was raised to the true volume a
  too-deep pocket took part 5 to 0.978.
- Part 9 drops most with a 150 mm pocket from the front; without ground truth it is unknown whether
  all of it was excess.
- `HLRBRep_PolyAlgo` fails on many candidates of parts 5 and 9; those candidates are skipped.

Step 3 with the real model (Qwen3-30B-A3B-FP8 on the Brev box, thinking off), 2026-11-11, at 47dd503:

- Eval sample, 24 parts, on the Windows PC: score 0.8287 -> 0.8293, sampled IoU 0.8246 -> 0.8252.
  Only parts 51 and 75 changed; no part got worse. As expected the eval briefs give the model
  nothing beyond `facts.json`, so this mainly shows that the checks keep wrong cuts out.
- Practice set, volume / true volume after step 3: part 7 2.20 -> 2.14, part 9 1.89 -> 1.86, the
  others unchanged. Most proposed cuts were rejected (part 5: 0 of 10, part 8: 2 of 8, part 7: 6 of
  7 kept, part 9: 5 of 5). Without ground truth it is unknown whether the kept cuts on 7 and 9 removed
  only excess material.
- With Qwen3's thinking on, the thinking alone used the 6000 tokens (25 s) and no answer came.
  Without it an answer takes 1 to 5 s.

**Steps 1 + 2 score lower on the Windows PC than in the table above: 0.829 instead of 0.839 (IoU
0.825), same 24 parts, 2026-11-11.** Simple and medium are equal; the difference is parts 51 (0.14 ->
0.04) and 99 (0.26 -> 0.11), whose top/bottom cut (316 loops on part 51) times out at 30 s here.
On part 51, with the machine idle, the fitted outline did not finish in 300 s and the traced pixels
took about 96 s. The code from before 2bc65c8 (2cfc439) also times out at 30 s on both, so it is the
machine, not a code change. The table above was measured on the ARM laptop.

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

## Known weaknesses

- Pockets are found only where a straight view shows their outline as lines and a side view shows
  their depth as a line; enclosed cavities and depths hidden in every side view are not found.
  Parts 6, 7 and 9 are still 1.9 to 3.3 times the true volume.
- Outlines that are neither straight nor circular (splines, ellipses of slanted cylinders) stay
  polylines. Practice outputs are now 0.05 to 1.3 MB (4.4 MB in total, was 44 MB).
- The timeouts make the output depend on how fast the machine is: a cut that finishes here may time
  out on the organisers' machine and give a different solid.
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
