# Pipeline plan: safe runs, one judge, shared evidence, a model that proposes

Written 2026-11-12 for the agent that implements it. Read `CLAUDE.md` and the four files in
`docs/memory/` first; this plan assumes them. Numbers quoted here are in `knowledge.md` under
"Research round of 2026-11-12" unless another source is named.

## Why this plan

- Simple parts are solved (sampled IoU 0.994 on the 8 simple parts of the eval sample). Complex
  parts are not: practice 6, 7 and 9 hold 2.76, 1.94 and 1.73 times the true volume.
- What is missing there is the depth of recesses and the thickness of walls. Lines and tones say
  where a recess is, not how deep; the true volume says how much, not where. Three research rounds
  found no deterministic step that closes this outside the parts it was developed on (+0.004 on
  36 parts it had not seen).
- A model can read structure from the views ("crankcase hollow open to the base"), but its numbers
  are guesses. So the model proposes and a deterministic judge decides.
- The judge the later steps use today passes most wrong cuts: 175 of 307 on single column cuts.
  A stricter rule passed 30 of 307. Any proposer that guesses needs the stricter rule first.
- Today a crash or the 20-minute kill can leave cases without a file, which scores 0.

## Target architecture

| layer | what | status |
| --- | --- | --- |
| 1. Base | box, silhouette cuts, isometric support, holes, rounds, curved recesses | exists, unchanged |
| 2. Evidence | one table per case: regions of each straight view with kind, the solid's depth there, and flags | new (phase 2) |
| 3. Proposers | level faces, shading, pockets (exist) and the model (new, phase 3); all read the table and emit cuts | partly new |
| 4. Judge | one accept rule for every proposed cut | new (phase 1) |
| 5. Finish | split faces, export | exists |

Order inside layer 3: large structural cuts first, fine cuts after. Reason: a cut raises IoU only
if the share of true excess in it exceeds r / (1 + r), r being our volume over the true one. Every
accepted large cut lowers r, and with it the precision the next cut needs.

## Rules for the implementer

- Work on a branch off `feat/visual-hull`. Never push to `main`; that is a submission.
- Do not change the entrypoint, the output names or the units.
- Do not run the 266-part eval. Use `uv run python tools/eval_hull.py --iou` (24 parts, cached).
- No threshold may be chosen by looking at the 9 practice parts. Choose on eval parts; use the
  practice set only to look at results.
- Other sessions edit this repo at the same time. Re-read a file before editing it.
- One phase per commit series. After each phase update `docs/memory/state.md`, and move answered
  items out of `open-questions.md`.
- Eval part 99 does not give the same solid twice. Leave it out of before/after comparisons, or
  compare on one cached solid.
- The sampled IoU of `eval_hull.py` uses 1500 points, so it cannot see changes under about 0.01
  per part. For the gates below, compare with at least 20 000 points (`IOU_POINTS`).

## Phase 0: every case always has a valid file

Files: `scripts/main.py`, `src/agentic_cad/hull_agent.py`, `src/agentic_cad/export.py`.

What is wrong today (read in the code):

- `process()` deletes the case's output at the start (`main.py:71`) and `generate_cad` writes it
  only at the very end. A segfault in the main process, or the organisers' kill, leaves this case
  and every later case without a file.
- If the final solid does not survive the `.stp`, the export falls back to the solid before all
  later steps, losing every good step with the bad one.

Changes:

1. **Boxes first.** Before any other work, write the bounding box of every case
   (`write_fallback`). Remove the `unlink` in `process()`; the box replaces a stale file.
2. **Checkpointed writes.** Add `export.checkpoint(solid, output_path, keep_faces)`: tidy unless
   `keep_faces`, write to a temporary file in `/tmp`, read it back and check it
   (`reads_back_valid` already does this; reuse the written file instead of writing twice), then
   copy it next to the output under a temporary name and `os.replace` it onto
   `test_case_<n>.stp`. Return whether it was written. The output directory must never be left
   holding a file with another name: remove leftovers at start and at exit.
3. **Hull and holes for every case, then the later steps.** Second pass: `build_hull` and
   `cut_holes` for each case, then a checkpoint. Third pass: `refine` for each case. Keep per case
   what `refine` needs (the solid, `agent.volumes`, `agent._stage`). `CaseTrace` keeps a list of
   tokens, so entering a case twice looks supported; verify that the dashboard still shows one
   case with all its steps, and fix `trace.py` if not.
4. **A checkpoint after every later step that changed the solid** (in `refine`). A step whose
   result fails the checkpoint is dropped and the solid before it is kept. This replaces the
   fallback in `generate_cad`.
5. **Order of the third pass:** cases with the largest volume ratio first. It matters only when
   time runs short; those cases have the most to gain.
6. Keep the time share logic (`RUN_BUDGET`, `agent.deadline`) as it is, applied to the third pass.

Not in this phase: running each case in its own process. It would also bound the hull by the
deadline, but tracing lives in the main process. Note it in `open-questions.md` if it stays open.

Done when:

- `uv run pytest` passes and `uv run python scripts/main.py` gives 9 valid solids with the same
  volumes as before the change (within 1e-6 relative; compare `agent.volumes` or read the files).
- Killing the run with SIGKILL at a random moment leaves 9 files that all read back as one valid
  solid, and nothing else in the output directory. Try it at least five times.
- A step made to raise, and a step made to return an invalid solid, cost only that step.
- The image builds and runs as a non-root user with a read-only input directory.

## Phase 1: one judge, fast enough to ask often

New file: `src/agentic_cad/judge.py`. Touched: `verify.py`, `shading.py`, `pockets.py`, `depth.py`.

1. **Fast drawing.** Drawing 8 views costs 1.4 to 1.7 s on eval 236 and 38; the boolean costs
   0.03 s. A prototype that fills large triangles with `cv2.fillConvexPoly` drew the same views in
   0.09 to 0.14 s (`runs/_research_scratch/photometric/common.py`). Port it into
   `verify.rasterise`. It was measured on blocky parts only; do not expect the gain on parts with
   many small triangles. Done when, on the cached solids of the 24 eval parts and the 9 practice
   parts, masks and face maps differ on at most 0.1 % of the pixels from the old drawing,
   `verify.check(...).score` agrees to 0.001, and the time per check is reported per part.
2. **The judge.** Port `maps` and `delta` from the scratch file `runs/_research_scratch/round2_accept/r2common.py`. Per
   view and pixel it computes four errors of a candidate against the input view:
   - tone: |observed tone - tone the table gives the candidate's normal|, clipped at 30 levels,
     away from lines and edges;
   - mask: silhouettes differ;
   - shp: a sharp edge of the candidate with no observed line within 2.5 px;
   - rec: an observed line with no candidate edge within 2.5 px.

   Interface: `Judge(case_dir, dims, observed, table)`, `judge.rate(base, candidate) -> Verdict`
   with the change of each term, the volume ratios and `accepted`; the maps of the base are cached
   and replaced when a candidate is accepted. Accept when: the candidate is valid, keeps at least
   97 % of the true volume, brings the volume ratio closer to 1, tone + mask + shp does not
   rise, and the mask term alone does not rise. The last condition comes from the model
   experiment: without it a large tone gain bought a broken silhouette (practice 9, 28 000 px).
   The rec term is reported and not used (round 3: it cannot say which side of a line moves).
3. **Switch.** `HULL_JUDGE=score` (today's rule, default) or `local`. Wire it into `shading.py`,
   `pockets.py` and the column cuts of `depth.py`. Leave `rounds.py`, `curved.py`, `split.py` and
   `iso_support.py` on the score rule: the local rule was measured on column cuts only.
4. **Measure, then decide the default.** Run the 24 parts with both settings. Report per part.
   - Gate to make `local` the default: mean sampled IoU not lower, no part lower by more than
     0.01, and fewer cuts that remove real material (count them with the exact boolean on the
     parts where it works).
   - Known risks to look at by hand: a right cut whose neighbours are still uncut leaves walls the
     views do not draw, which raises shp (practice 6; the scratch code has an excused variant,
     `shp_fix`); on practice 9 the rule refused a shading cut that looked right.
   - If the gate fails, keep `score` as the default and keep the judge for phase 3 only.
5. **An evidence requirement, as a second variant.** "Does not rise" passes every floor that no
   view sees. Variant: the tone term must fall by a minimum per changed pixel. This has not been
   tested; measure it next to the plain rule and keep it only if it removes wrong cuts without
   losing more right ones than it removes.
6. **Budgets by count.** `shading.BUDGET` (45 s), `pockets.BUDGET` (30 s) and `split.BUDGET`
   (15 s) stop steps by the clock, so a slower machine gives another solid (practice 7 and 9
   today). After the fast drawing, replace each with a cap on what is counted (regions,
   candidates, passes) and keep the clock only through `agent.out_of_time()`. Done when two
   practice runs, one alone and one with the machine loaded, give the same volumes and face counts.

## Phase 2: one evidence table per case

New file: `src/agentic_cad/evidence.py`. Touched: `shading.py`, `pockets.py`, `depth.py`,
`split.py`, `hull_agent.py`.

Today each step calls `verify.observe`, `regions.find` and `lines.find` for itself, and
`agent.lines_found` hands lines from pockets to split by hand. These four steps are 90 % of the
run time.

1. `Evidence` holds, for the case: the observed views, the chosen tone table, the drawings of the
   current solid, the regions of each straight view (`regions.find`) and the line pieces
   (`lines.find`). Built once after the holes, kept on the agent, and refreshed for the views a cut
   changed when a cut is accepted.
2. Each region gets, besides its kind (facing, slanted, curved):
   - the solid's depth under it (median and spread);
   - the length of line pieces under our surface inside or along it;
   - whether a neighbouring facing region across a line is coplanar with it in our solid (a line
     between two facing regions is a depth step for 175 of 179 pairs; coplanar in our solid means
     wrong for 18 of 22);
   - the misfit between its tone and the tone of the solid's face under it;
   - whether the isometric view confirms the solid's face (`normals.refine`).
3. The steps read the table instead of computing their own. This sub-step must change no output:
   same volumes on the 24 parts and the practice set.
4. A marked image per view: the view with the region outlines and their numbers drawn in.
   Attached to the trace (it appears under "Extracted" without a UI change) and used as the
   model's input in phase 3.

Not in this phase: new cuts from the flags. The coplanar-pair cuts were measured at +0.004 outside
the parts they were developed on, and using the isometric confirmation as a guard cost eval 38
three right cuts. The flags are for ordering and for the model.

## Phase 3: the model proposes

The experiment on practice 5, 7 and 9 is in "Experiment result" at the end: promising, not
proven. So this phase starts with a test against ground truth and is built only if that holds.

0. **Test on parts with ground truth first.** Take the hard set of phase 4, let the model that will
   really be served (not a stand-in) write proposals from the inputs alone, apply them with the
   scratch harness (`round4_model/apply_ops.py`) and measure sampled IoU before and after.
   Do not tell the model the excess volume in one of two runs, to see how much of the result
   depends on it. Go on only if the mean IoU rises and no part falls by more than 0.01.

New files: `src/agentic_cad/ops.py`, `src/agentic_cad/propose.py`. Hooked into `LATER_STEPS` after
`depth` and before `shading` (structural cuts first; confirm by measuring both orders).

1. **Operations (`ops.py`, deterministic).** A cut is a typed record in millimetres, in the frame
   of the solid (origin at the bounding-box minimum corner):
   - `box` (min, max), `cylinder` (two points, radius), `prism` (axis, polygon, range),
     `halfspace` (point, normal);
   - `region_floor` (view, region number, depth): the column over a region of the evidence table,
     cut down to a depth. This is the form to prefer: the model names a region and gives a number.

   Each becomes a tool solid and is cut through `agent.cutter.run` with a timeout.
2. **Refining a proposal before judging it.** The model's depth is a guess. For each operation
   with a depth, try a small fixed set of depths around it, plus the depth at which the removed
   volume would close the gap to the true volume when the operation is the only candidate left,
   and let the judge choose; among accepted depths take the shallowest. The judge cannot choose
   between depths for floors no view sees (the error is the same at 1 and 1.5 times the true
   depth), so there the guess stands and the volume guard is the only limit.
3. **The call (`propose.py`).** One request per case to the OpenAI-compatible endpoint
   (`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`, see README "Configuration"): the eight
   views, the marked images, `facts.json`, the brief, the region table as JSON and the current
   volume ratio; temperature 0; the answer is JSON in the schema above with a confidence per
   operation. Tell the model that the brief can be wrong and the views decide (practice 5 and 9).
   A timeout, a refused connection or an answer that does not parse means the step does nothing.
   The step is off when the variables are not set, so the pipeline without a model is unchanged.
4. **Applying.** In order of confidence; each on top of what was accepted so far; a checkpoint
   after each accepted one (phase 0). After a pass, try the rejected operations once more: on
   practice 7 two operations showed no evidence against the base and clear evidence once the
   large pocket above them was cut.
5. **Time.** One call and at most a fixed number of operations per case, inside the case's share.

Done when: with the variables unset, all outputs are identical to phase 2; with a model, the hard
set (phase 4) improves on average and no part of the 24-part sample gets worse by more than 0.01.

## Phase 4: a hard set to measure phase 3 on

The 24-part sample is mostly easy and the practice set has no ground truth.

- Choose about 20 eval parts whose solid after the holes holds more than 1.5 times the true
  volume, from results that are already cached (`runs/_eval_cache`; the step 1 results of 259
  parts from 2026-11-11). Do not run the 266 parts to find them.
- Leave out parts that fill under 5 % of their box (cages like eval 51 and 99): 6 of 266, and no
  practice part is one.
- Fix the list in `tools/eval_hull.py` as a named sample (`--hard`), cached like the default one.
- The eval briefs are templates. That is acceptable: on the practice parts the model's useful
  reading came from the views, and two of three briefs misled it.

## Not to build

These were tried in the research rounds and did not hold:

- Reading depths from the briefs: 6 of 417 numbers are not already in `facts.json`, none a depth.
- Depth from tone: the renders hold no depth cue.
- Carving the deepest material until the volume fits: made eval 38, 236 and 110 worse.
- Intersecting the solid with its mirror image: 0.8712 to 0.8715.
- A stand-alone step for coplanar pairs: +0.004 outside its development parts.
- Work aimed at eval 51 and 99: half the loss of the sample, a part type the practice set lacks.

## Decisions that stay with the user

- When to merge to `main` for the first submission. It would also show whether the score is
  volume overlap or surface based (practice 6 near 0.3 against 0.55 or more), which decides
  whether carving the hollow of a shell is worth building.
- Which model the endpoint serves and when it is up.

## Scratch material

Prototypes are in `runs/_research_scratch/` (git-ignored, never in the image; copied there from
the research session). Their imports assume the session's old directory: fix the paths (`HERE`,
`ROOT`, `sys.path`) before running one.

- `round2_accept/r2common.py`: the error maps and their differences (phase 1).
- `photometric/common.py`: the fast drawing (phase 1).
- `round3_combined/combo.py`: the coplanar-pair detector and the accept variant without rec.
- `round4_model/`: the model experiment: `proposal_<n>.json`, `apply_ops.py`, `r4ops.py` (phase 3).

## Experiment result

2026-11-12, scratch only (`round4_model/`). Three agents stood in for the model, one per part.
Each saw only the brief, `facts.json`, the 8 views and the current volume ratio, and wrote a list
of removal operations in millimetres. A harness applied them to the solids of run
`20261112-064134-d0e0` and judged each with three rules, in order of confidence.

Our volume over the true volume:

| part | operations | before | local rule without rec | local rule with rec | today's score rule |
| --- | --- | --- | --- | --- | --- |
| 5 | 18 | 1.224 | 1.000 (17 kept) | 1.000 (17) | 0.985 (18) |
| 7 | 13 | 1.936 | 1.297 (8) | 1.308 (7) | 1.248 (13) |
| 9 | 6 | 1.729 | 1.034 (4) | 1.034 (4) | 1.058 (2) |

All final solids read back as one valid solid; 2.5 to 7 s per operation.

What it shows:

- One operation carries most of it: the top pocket of practice 7 (1.936 to 1.527) and the
  crankcase hollow of practice 9 (1.729 to 1.073). By eye both are where the views show a cavity;
  their floors are flat or V-shaped where the part's are curved.
- The model corrected the brief from the views on all three parts (practice 5 is a cylinder head,
  not a "thin-walled cover"; practice 9's hollow is not in the brief at all).
- Today's score rule accepted all 13 operations on practice 7 and cut practice 5 below the true
  volume; the local rule refused 5 and 1.
- The local rule accepted two operations on practice 9 that the proposer itself rated 0.1 and
  expected to remove real rings: the tone error fell and the silhouette broke by 28 000 px. Hence
  the separate mask condition in phase 1.
- Depth: on the pocket of practice 7 the error is lowest at the proposed depth and the rule
  refuses 1.5 times it; on a port of practice 5 the error is flat from 0.5 to 1.5 times. So the
  rule bounds a depth only where a view sees the floor.

What it does not show:

- Whether the cuts are right. The practice parts have no ground truth, and a volume ratio near 1
  is not an overlap: the proposers were told the excess and sized their operations to it.
- What a served model does. The stand-ins were large models that took 2 to 4 minutes per part.

