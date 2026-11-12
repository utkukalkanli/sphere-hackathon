# Project state

Last updated: 2026-11-12 (compacted; the long version with every intermediate measurement is
`git show bda2104:docs/memory/state.md`).

This file describes the present. Findings about the inputs and the code are in `knowledge.md`,
reasons in `decisions.md`.

## Summary

- **First submission: 2026-11-12, commit `fdcc8c6` on `main`** (CI run 43239297964: tests and
  submit green). The deterministic pipeline, model step off. No score seen yet: the leaderboard's
  address is not in the README.
- `main` now has this branch's tree. A teammate's other pipeline (LLM CadQuery loop, hull and
  graph engines, vLLM serving script, Brev guides; 12 commits up to `70b1a15`) was on `main` and
  was merged with `-s ours` (user, 2026-11-12): its files are in the history, not in the tree.
- Work goes on on `feat/phase0-safe-runs` (same commit as `main`). Plan phases 0 to 3
  (`docs/plan-pipeline.md`) are committed.
- Every step of the submitted pipeline is deterministic. The model step is off in the image: the
  model (Gemma 4 31B FP8 on the team's Brev H100, vLLM) is reachable only through an SSH tunnel
  from the laptop (`.env` for the dashboard), not from the image or the organisers' machine.
- Latest whole-pipeline numbers: sampled IoU 0.8929 on the fixed 24 eval parts (simple 0.9943,
  medium 0.9260, complex 0.7584); practice parts 1, 2, 3, 4 within 3 % of the true volume, 8 at
  1.06, 5 and 9 at 1.2, 7 at 1.9, 6 at 2.8.

## Pipeline, in run order

| step (trace name) | module | switch | measured when it went in |
| --- | --- | --- | --- |
| box | `scripts/main.py`, `export.py` | | |
| silhouette cuts: front/back, right/left, top/bottom | `visual_hull.py`, `outline_fit.py` | `HULL_OUTLINE`, `HULL_TIGHT_SHARE`, `HULL_THIN_SHARE` | fitted outlines: volume score 0.832 to 0.839, faces 1245 to 232 a solid; tight cuts and thin walls: hull + holes IoU 0.8335 to 0.8447 (eval 75, 51) |
| isometric support | `iso_support.py`, `hull_agent._with_iso` | `HULL_ISO_EXCESS` (1.5), `HULL_ISO_MIN_GAIN` (1 %) | hull + holes IoU 0.8447 to 0.8541, kept on the 4 parts it ran on (99, 38, 171, 51), none lower |
| holes, blind spans, skins | `holes.py`, `spans.py` | `HULL_HOLE_SPANS=0` | holes: IoU 0.824 to 0.834, cut on 14 of 24, none worse. Spans and drill points act on practice 8 only; skins on practice 9, 5 and eval 123 |
| mouths (`open_mouths`) | `mouths.py` | `HULL_MOUTHS=0`, `HULL_MOUTH_FACES=0` | practice 9 whole pipeline 1.727 to 1.205 times the true volume; eval 123 and 171: of 334 sampled points removed none is in the truth |
| rounds (`cut_rounds`) | `rounds.py` | `HULL_SKIP=rounds`, `HULL_ROUNDS_DISC=0` | IoU 0.834 to 0.847 (simple 0.960 to 0.994); "disc": cuts 6 of 266 eval parts, none lower; practice 6 3.81 to 3.31 |
| curved recesses (`cut_curved`) | `curved.py`, `tone.py`, `regions.py` | `HULL_SKIP=curved` | no cut on the 24; 5 of the 37 lathe-type eval parts, none worse (249: 0.725 to 0.981); practice 2 1.105 to 1.018 |
| level faces (`level_faces`) | `depth.py` | `HULL_SKIP=depth`, `HULL_DEPTH_SUPPORT=0` | IoU 0.847 to 0.854; supported cuts 0.8668 to 0.8791 (38: 0.628 to 0.933; 99: 0.252 to 0.241, accepted); practice 6 3.31 to 2.77 |
| model (`model_cuts`) | `propose.py`, `ops.py`, `judge.py` | off unless `OPENAI_BASE_URL` and `OPENAI_MODEL` are set; `HULL_PROPOSALS=<dir>` replays files | 12 eval parts with excess, Gemma 4 31B, same start solid, 20 000 points: IoU 0.9181 without, 0.9414 with (236: 0.796 to 0.990; 171: 0.759 to 0.844; 74: 0.899 to 0.918; 3: 0.918 to 0.912; 38 and 110 down 0.007); 24 to 332 s a part (below) |
| shading (`shading_depth`) | `shading.py` | `HULL_SKIP=shading`, `HULL_SHADING_SLANTED=0`, `HULL_SHADING_DEEP=0` | IoU 0.854 to 0.861 (110: 0.79 to 0.86, 236: 0.70 to 0.73), none lower |
| pockets (`cut_pockets`) | `pockets.py`, `lines.py` | `HULL_SKIP=pockets` | eval 236 1.354 to 1.254 times the true volume (IoU 0.740 to 0.796); 22 of 24 untouched, none lower |
| split (`split_faces`) | `split.py`, `lines.py` | `HULL_SKIP=split` | moves no material; splits faces on about 10 of the 24 |
| export | `export.py` | | |

What each step does:

- **Silhouette cuts.** The box of `facts.json`, cut through wherever both opposite views are
  white. Outlines are rebuilt from lines and circular arcs first. A silhouette with more than 1 %
  of its pixels in walls up to 2 px thick is traced along pixel edges, all others through pixel
  centres. A cut that matches its view is kept down to 90 % of the true volume.
- **Isometric support.** Only when the straight cuts leave more than 1.5 times the true volume:
  the isometric silhouette is cut first, on the box, the straight cuts after it. Kept only if
  every cut went through, at least 1 % of the true volume is gained, the lines of the views do
  not object and the solid still fills the three straight silhouettes. The stand-alone fourth
  cut (`use_iso=True`) is off.
- **Holes.** Every listed hole is cut as a cylinder (shortest listed length); a thinner coaxial
  boss inside a bore is kept. An end within 2 px of our surface is carried through that skin
  (`spans._open_ends`). A span along x, y or z that both views along it show closed is cut as two
  blind holes, each half of the room it has, with a drill point where the cone count of
  `facts.json` fits.
- **Mouths.** A listed hole end buried in our solid whose circle a view draws (75 % of it, and no
  edge of ours explains the line) is a mouth: what lies between it and that camera is removed.
  Where two or more mouths of a straight view lie on one plane in one evenly toned area, the area
  is cut down to that plane, kept only if no view's outline loses more than 0.05 %.
- **Rounds.** Along a listed boss or bore axis, where both side views show the same strip centred
  on the axis, the square section is cut to the revolved outline. Needs a listed boss of that
  radius, or a disc seen along the axis: with only lathe faces, or ("disc") every stretch where
  the two side views agree to 3 %, kept if the verifier's line term does not drop.
- **Curved recesses.** Seen along a listed axis, a ring that is flat in our solid and changes
  tone in the view is a curved face. It continues the neighbouring profile if the solid has its
  start, else every cone angle and torus radius of `facts.json` is tried and the verifier picks.
  Dropped if its tones are more than 10 levels off the table, fit worse than the flat face, or
  it reaches through the box.
- **Level faces.** An area between lines with the facing tone is one face at one depth: where it
  covers several levels of the solid it is cut to the lowest (if that level has 5 % of the
  area). Supported cuts: with no level under it, down to the highest level at which a
  perpendicular view draws a line along 90 % of the face seen edge-on; an area along straight
  edges of known level is cut to the steepest planes through those edges inside the solid.
- **Model.** One request per case (six straight views with regions numbered, two isometric
  views, `facts.json`, brief, region table, volume ratio unless `HULL_MODEL_TELL_VOLUME=0`); the
  answer is a list of typed cuts (box, cylinder, prism, halfspace, region_floor). Each is cut,
  most confident first, and kept only if the local judge accepts it, the views fit strictly
  better and the file reads back. Refused ones are tried once more. A region_floor is tried at
  0.5, 0.75, 1 and 1.25 times its depth, shallowest accepted wins.
- **Shading.** A facing area enclosed by lines is slid down the view direction and projected
  into the isometric view; the depth is where its pixels show that side's tone and its outline
  lands on lines. Up to 3 passes. Slanted faces only when hung on an edge of known depth. A
  second, tone-only search takes floors of at least 10000 px. A face the isometric view already
  confirms is never touched. Depths come out 3 to 5 px shallow, the safe side.
- **Pockets.** A figure of lines inside one flat face of ours and under it (closed, or ending on
  other lines and closed straight across) is a pocket outline. Depth: where half of the outline
  lands on isometric lines and 0.25 more than at a usual depth.
- **Split.** A line of a straight view through the inside of one face of ours, confirmed by the
  isometric view to lie on our surface, is drawn on as an edge. No cap on the face count. Seams
  of full cylinders, cones and tori are left out. Runs last; the export then skips `tidy`.

Not steps:

- **Verifier** (`verify.py`): draws a solid from the 8 cameras; score = lines x silhouettes x
  volume ratio. Ground truth scores 0.96 on the 24 parts, our hull + holes 0.71, the box 0.24. A
  check takes 0.5 to 1.8 s.
- **Local judge** (`judge.py`, `HULL_JUDGE=local`): per-pixel tone, mask and edge errors over
  the 8 views; the outline may worsen by 0.2 % of a view's part. Default is `score`; only the
  model step uses the judge.
- **Evidence** (`evidence.py`): views, drawings, lines and regions computed once per case and
  shared by all later steps; `evidence.rows` and `evidence.marked` feed the model and the trace.
  No step decides by its flags.

In the code and off: shared datums (`HULL_DATUMS=1`), edge tracing everywhere
(`HULL_TRACE=edges`), thin-wall guard (`HULL_THIN_GUARD=1`), isometric support from 1.05
(`HULL_ISO_EXCESS=1.05`). Unused inputs: the brief (except its Envelope line as a fallback, and
as model input), `context.png`, and of `facts.json` the surface area, face and edge counts,
genus, partial arcs and fillet radii.

## Run safety and time

- `scripts/main.py` makes three passes: the box of every case; hull and holes of every case; the
  later steps, cases with the most excess volume first. A file is only replaced through
  `export.checkpoint` (written to `/tmp`, read back, renamed in place if it is one valid solid),
  after every later step that changed the solid. A step whose file does not read back is
  dropped, alone.
- One clock: `HULL_RUN_BUDGET` (17 min), shared among the cases still to do; in the third pass a
  case may use two shares (`CASE_SHARES`). No step has its own budget. A case with under 5 s for
  its hull keeps its box.
- Every boolean runs in a child process with a timeout (30 s straight cut, 40 s isometric cut and
  holes). A cut that fails, times out or leaves under 97 % of the true volume (90 % for a
  matching silhouette cut) is undone.
- Each straight cut is projected back onto its view (3 px band, 0.2 % of the area); on failure it
  is repeated with the traced-pixel outline. Holes refused together are retried one entry at a
  time.
- Later steps keep a cut if the verifier's score drops by at most 0.003 (supported level-face
  cuts also if 1 % more of the views' lines become boundaries and the outline is untouched).
- An exception costs only the direction or step it happened in.
- Checked: SIGKILL at 7 moments from 1 to 316 s left 9 valid solids and nothing else
  (the boxes exist 0.7 to 1.1 s after the start). Two practice runs under different load gave
  identical volumes and face counts on all 9.

## Measurements

Local score: `min/max` of our volume and the true one; sampled IoU with `--iou` (1500 points,
blind to changes under about 1 %; use `IOU_POINTS=20000` for before/after).

Fixed 24 eval parts, whole pipeline, `tools/eval_hull.py --iou`:

| when (2026-11) | volume score | sampled IoU | note |
| --- | --- | --- | --- |
| 11, steps 1 + 2 | 0.839 | 0.834 | |
| 11, + rounds, level faces | | 0.854 | |
| 11 night, + shading | 0.867 | 0.861 | |
| 12 02:35, + iso support, new depth, pockets, split | 0.885 | 0.882 | 406 s, 4 processes |
| 12 afternoon, + hole work (6847e04) | 0.8973 | 0.8929 | 346 s; 295 faces a solid |

- Latest by tier: simple 0.9943, medium 0.9260, complex 0.7584. Lowest: 51 (0.14), 99 (0.44),
  171 (0.76), 236 (0.80), 110 (0.86). Eval 75 ends below the true volume on purpose.
- Volume score after each step, run of 02:35 (the last per-step breakdown):

  | | top_bottom | iso | holes | rounds | depth | shading | pockets | split | IoU |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | simple (8) | 0.956 | 0.956 | 0.956 | 0.994 | 0.994 | 0.994 | 0.994 | 0.994 | 0.994 |
  | medium (8) | 0.839 | 0.845 | 0.848 | 0.850 | 0.870 | 0.890 | 0.892 | 0.892 | 0.891 |
  | complex (8) | 0.718 | 0.740 | 0.764 | 0.764 | 0.765 | 0.768 | 0.768 | 0.768 | 0.760 |
  | all (24) | 0.838 | 0.847 | 0.856 | 0.869 | 0.876 | 0.884 | 0.885 | 0.885 | 0.882 |

- Step 1 alone on 259 of the 266 parts (2026-11-11): mean 0.837, median 0.932 (simple 0.874,
  medium 0.856, complex 0.747).
- Accept rules on the steps' own cuts (23 eval parts, `tools/check_judge.py`): score rule mean
  IoU 0.9139, kept 13 right and 0 wrong cuts, refused 1 right and 6 wrong; local rule 0.9046,
  kept 12 and 0, refused 15 right and 6 wrong.

Practice set, our volume / true volume (no ground truth, so only excess shows):

| part | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| steps 1 + 2 | 1.07 | 1.23 | 1.09 | 1.24 | 1.23 | 3.81 | 2.20 | 1.01 | 1.89 |
| 12 morning (run `20261112-064134-d0e0`) | 1.003 | 1.018 | 1.000 | 1.028 | 1.224 | 2.759 | 1.936 | 1.002 | 1.729 |
| 12 afternoon, latest | 1.003 | 1.018 | 1.000 | 1.028 | 1.225 | 2.759 | 1.943 | 1.063 | 1.205 |
| faces, ours of the part's | 19 of 17 | 22 of 12 | 9 of 9 | 542 of 178 | 152 of 172 | 799 of 122 | 733 of 628 | 333 of 306 | 225 of 371 |

- Part 8 rose because 12 holes are no longer cut through; the old 1.002 was a cancelled error
  (`decisions.md`, Evaluation). Part 9 fell through the mouths.
- All 9 files read back as one valid solid. The pockets cuts on practice 5, 7 and 9 (slivers on
  9) and the shading cuts on 4 have not been checked by eye.
- Against the views (`tools/check_views.py`, tone off on share of part pixels): practice 9 4.2 %
  (22.1 % that morning), practice 5 4.0 %, 8 6.2 %, 7 16.7 %.
- Model step with the served model (2026-11-12, `tools/check_judge.py --rules score model`).
  Asked for cuts with numbers, Gemma handed back the hole list of `facts.json` and the depths of
  the region table (practice 5: 20 proposals, 0.135 cm3 removed). Asked only which numbered
  regions are floors of recesses, with our solid drawn beside the isometric views, it named right
  ones: eval 236 12 of 12 kept, 171 7 of 8. The depth is searched by the pipeline (9 depths,
  shallow to deep, the judge keeps the best). The loss on eval 3 (a tyre, 0.918 to 0.906) was four cuts in
  regions of kind "curved"; since then only "facing" regions are searched (0.912). Stopping the
  search when two depths in a row were no better was tried and taken out: eval 236 fell from
  0.990 to 0.801. The plan's gate (mean up, no part down by more than 0.01) holds on these 12
  parts; the step has not been run on a hard set, and its time (up to 332 s a part, all nine
  depths for up to 12 recesses) has not been bounded or measured on the practice set.
- Replay of proposals written by three large stand-in models for older solids
  (`runs/_research_scratch/round4_model/`): practice 5 1.225 to 0.990, 7 1.913 to 1.316, 9 1.205
  to 1.123. Not a measurement of right cuts: the proposers were told the excess.

Time: the 9 practice parts take about 7 minutes on the loaded laptop (part 7 about 130 s, 9
about 90 s, 6 about 70 s); shading, split, level faces and pockets are 90 % of it. The amd64
image, emulated, took 10.5 minutes at phase 0.

## Submission readiness

- `artifacts/outputs` holds the files the `linux/amd64` image of commit `bda2104` wrote on
  2026-11-12 (non-root user, read-only root and inputs, 11.9 minutes emulated, all 9 one valid
  solid). It was run with `HULL_RUN_BUDGET=7200`: emulation is 3 to 4 times slower than the
  laptop and the case shares would have skipped steps on practice 7. Against a native laptop run
  of the same code the volumes agree to 2e-6 on all nine.
- **A model in a submission needs a public endpoint**: the port opened on Brev (TCP, not the
  login-protected HTTP port), vLLM with a real `--api-key`, and the three values as `ENV` in the
  Dockerfile and in `team-d.env`.
- GitHub Actions variables for team-d are set, so a push to `main` submits.
- Other branches on origin, not read for this file: `feat/llm-cad-agent` (a teammate's model
  serving), `ananas`, `nar`.

## Not done

- Model: the time of the step bounded and measured on the practice set, the hard set
  (`tools/find_hard.py` is written and not run: it needs hull and holes on all 266 parts, which
  needs the user's go), a volume-closing depth, depths for boxes and prisms.
- `tools/eval_hull.py` calls `build_hull`, `cut_holes` and the mouths itself, not `carve`: a
  step added to `carve` must be added there too.
- Several dashboard changes have not been looked at in a browser (below).

## Known weaknesses

- **Blind recesses and inner cavities** are still the largest loss (eval 51, 99, 38, 236;
  practice 5, 6, 7). Shading finds a floor only if it is flat, in sight of an isometric camera,
  and square to a view or hung on a known edge; small unconfirmed floors are skipped.
- **The verifier cannot see a wrong cut** that leaves silhouettes and lines alone and brings the
  volume closer (eval 123, practice 5 with `curved.py` before its tone check).
- **A solid can be valid in memory and invalid in the file** (practice 4, a near-flat cone from
  the rounds step). Every later step is now read back; the cause, a fitted line 1.3 degrees off
  square, is not fixed, and part 4 loses one round cut to it.
- Outlines that are neither straight nor circular stay polylines (most extra faces: eval 51, 99).
- Timeouts and the run budget make the output depend on the machine's speed.
- The mouth-face rule and the blind-hole depth have never been measured against a ground truth.
- README.md's dashboard section describes the first dashboard.

## Tools

| tool | what |
| --- | --- |
| `tools/eval_hull.py [--iou] [--all]` | score on the fixed 24 eval parts (8 per tier), cached in `runs/_eval_cache` by source hash; the step 1 solid has its own cache |
| `tools/check_views.py SOLIDS_DIR test_case_<n> ...` | where a solid disagrees with its 8 views, no ground truth needed: tone off, lines lacking, edges not drawn; `--save` pictures. 15 s a part |
| `tools/check_verify.py` | verifier scores of ground truth, our solid and the box |
| `tools/check_judge.py` | grades each cut of level faces, shading and pockets against the ground truth, per accept rule |
| `tools/check_lines.py` | grades `lines.py` against the ground truth; cache in `runs/_lines_cache` |
| `tools/check_regions.py` | grades `regions.py` and `normals.py`; `--practice --save DIR` for a look by eye |
| `tools/check_normals.py [--table FILE.npz]` | tone against normal; writes the tone tables in `src/agentic_cad/tone_tables.npz` |
| `tools/check_shading.py` | shading step alone; fills `runs/_shading_cache` |
| `tools/find_hard.py` | picks a hard eval set; not run |
| `ui/server.py` | dashboard, reads `runs/` |

`normals.py` is a measurement only, used by no step.

## Dashboard (`ui/`)

- Left, what was given (brief, facts, input pictures, ground truth; its own slider; zoom and pan
  on the pictures). Right, our solid after the chosen step, the material that step removed in
  translucent blue. Floating over the boundary, "Extracted": the chosen step's images, lists,
  log and metrics. Below: the steps as one line of stops (name and volume ratio; tooltip with one
  sentence and the solid before and after) and "Diff" (truth, ours, difference; IoU, excess and
  missing volume computed in the browser).
- Every case shows the same 18 stops; a step that did not run is dimmed as pending, off, skipped
  or not run; steps outside the plan fold into the stop they happened in.
- Keys: D diff colouring (red excess, violet missing), C a colour per face, X section plane, S
  scores panel (per tier, worst first). Case and run lists filter by typing; the run list shows
  the runs of the case on screen with its score.
- Black background, no rules or nested frames, little text.
- Not yet looked at in a browser: the picture zoom controls, the run list per case, the
  "evidence" stop (it has no solid).
