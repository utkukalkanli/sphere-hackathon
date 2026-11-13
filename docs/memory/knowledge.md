# What we know about the task and the code

Facts that took reading or measuring to find out. Correct an entry in place when it changes.
Unless a date says otherwise, numbers are from 2026-11-11 or 12 on the fixed 24 eval parts.
Longer accounts of each finding: `git show bda2104:docs/memory/knowledge.md`.

## The task

- **Position and orientation of our solid do not matter; size and handedness do** (README,
  Outputs). The scorer registers the two solids; how is unknown.
- CI on a pull request runs pytest and builds the image; on a push to `main` it also submits.
  pytest fails if a practice part lacks its `test_case_<n>.stp` in `artifacts/outputs` or the
  folder holds another file.
- After code freeze the organisers run the image on the practice inputs and compare with the
  committed outputs (a mismatch is reviewed by hand), then on the hidden parts. They need
  `<team>.env` with every variable the agent reads.
- **Run limits, from the organisers (2026-11-12 22:03, Lorenz Plank)**: one hour for the 9 parts
  together, no limit per part; network access is allowed and hosted LLMs may be called. This
  overrides the README's 20 minutes. Code freeze is Friday 2026-11-13 08:00.
- The scoring (kickoff slides): 0.30 volume overlap + 0.30 surface match (at 0.5 to 10 % of part
  size) + 0.20 dimensions (2 % off loses 10 %, 20 % off loses all) + 0.20 through-holes counted
  exactly from topology, after a rigid alignment. Each team's best commit counts on the leaderboard.

## `facts.json`

Exact, measured from the ground truth, and given for the hidden parts too.

| field | gives | does not give |
| --- | --- | --- |
| `bounding_box_mm`, `volume_mm3`, `surface_area_mm2` | true sizes | |
| `faces`, `edges`, `face_types`, `through_holes_genus` | counts | |
| `cylindrical_holes_and_bores`, `cylindrical_shafts_and_bosses` | full cylinders: diameter, axis, count, centres (midpoint of the axis) | `lengths` is the set of distinct lengths of a group, not one per centre |
| `partial_cylinders_arcs` | diameter, concave or convex, face count | position, axis |
| `fillets_rounds_from_cylinders`, `fillets_from_tori_minor_radii`, `cones_half_angle_deg` | radii and angles with counts | position |

- An axis is `"X"`, `"Y"`, `"Z"` or a vector as text. Positions are in mm from the bounding-box
  min corner, in the frame of the views; our solid is built in that frame.
- A cylinder is full when its faces sweep 98 % of a turn. Counterbores, grooves and stepped
  bores are several entries on one axis.
- **One entry can be two opposite blind holes** (practice 8). Coaxial faces of one diameter are
  listed with one centre and the span from one outer end to the other. Its 8 d7 holes are listed
  40.2 long, the full height, and both views along them show a drilled bottom; the brief calls
  them "through". Their depth is nowhere. Our eval tool lists every stretch separately, so the
  eval set has no such case.
- **The cone count confirms such holes**: practice 8 lists 48 cones at 59 degrees (a 118 degree
  drill), two half faces for each of 24 blind holes. A drill point adds 0.6 radii.
- A listed hole that is not white in the views along its axis is not by itself wrong (12 groups
  on the practice set). It is wrong where it opens a line of sight both views show closed.
- **A hole listed up to a face can stay closed in our solid**: faces traced from a silhouette lie
  a fraction of a pixel outside the part's (0.25 to 0.31 mm on practice 9), so the cylinder ends
  under a skin. The volume barely shows it; every view of the mouth does.
- **A listed hole end is a point of the part's surface.** If a view looking at it draws its
  circle, it is a mouth and the part is empty from there to that camera (practice 9: 87 mm). The
  line must not be one our own solid draws: a coaxial hole on the far face projects onto the same
  circle (eval 123). The area around a mouth is not always its face (practice 7: a recess a
  quarter of a millimetre wider than the hole shows one circle).
- **Volume and surface together do not tell a hidden void from uncut open pockets.** Our volume
  and surface over the true ones: practice 6 (hollow) 2.76 and 0.81; eval 38 1.62 and 0.76,
  practice 5 1.23 and 0.81 (not hollow). A wall thickness solved from the volume and checked
  with the surface fits the parts that are not hollow better than practice 6.

## Briefs and `context.png`

- **Practice briefs are prose but hold almost no number `facts.json` lacks**: of 417 numeric
  tokens 360 are verbatim in `facts.json`, 51 derivable, 6 new (two hole centres of practice 5,
  angles). No depth of any feature that causes our excess. Each has an
  `**Envelope:** X x Y x Z mm` line.
- **They are wrong in places**: practice 6 "solid" (it is a shell) and "four-fold symmetric" (it
  repeats every 20 degrees); practice 8 blind holes "through", drill points "chamfers"; practice
  9 "exact mirror images" with bores offset 25.4 mm; practice 1 recess at Y = 7.0 where the view
  shows 0; practice 5 "thin-walled cover" for a cylinder head. The views decide.
- Eval briefs are generated from `facts.json` by a template and hold nothing more.
- `context.png`: the part in orange inside a grey assembly, from an unstated angle. 6 of the 9
  practice parts have one (not 7 and 8); the eval set has none.

## The views: geometry

- 1024 px, orthographic, white background, flat tan shading, dark lines on face boundaries.
  Hardly anti-aliased (3 grey levels), so no sub-pixel edge exists.
- The part is centred; half the image is the framed extent times 1.04. In the six straight views
  the silhouette's bounding rectangle is the box face, so the scale is exact.
- In the isometric views the frame fits the projected box or the part itself. The scale follows
  from the top view: the extent along (1, 1, 0) in mm is the part's width there in pixels
  (`verify._iso_scale`). A stretch can be 9 times shorter there than in a straight view.
- A solid drawn with `verify.draw` lands within 1 px of the part in all 8 views, both renderers.
- **The silhouette mask is wider than the part** (the dark outline counts as part), so tracing
  through pixel centres is closer than along pixel edges. A pixel is background when its darkest
  channel is above 215.
- A silhouette overlap below 1 is normal for thin parts: the ground truth gets 0.83 against its
  own view when the part is 12 px thick.
- Thin-walled silhouettes are rare and easy to tell: a 3 x 3 opening removes 14.5 % of the part
  pixels in eval 51's top view and at most 0.07 % in every other silhouette we have.
- **A silhouette cut can land below the true volume without being wrong** (eval 75: 0.956 with
  fitted and traced outlines alike).
- **Round sections come out square** from two side views: up to 4/pi = 1.27 times the volume.
- **What the silhouettes miss is open to a view, not enclosed**: after hull and holes 97 % of
  the excess has a free line of sight along an axis. What is missing is depth.

## The views: lines

- Lines are found as what a 5 px closing fills in, at least 28 levels darker than both sides.
  Missed: a line between two dark faces (practice 6: faces 55 to 70, line 30 to 45; a relative
  test in `regions.segment` gets most) and the boundary of a bright and a dark face (split at
  jumps of 24 levels; in eval renders that also splits curved faces into their triangles).
- **The organisers' renderer draws no line at the bottom of a groove** (practice 6): ridges have
  a line, valleys only a tone step of as little as 2 levels. Two faces meeting in a concave
  crease can be one area; `depth.py` skips areas under 90 % of their convex outline.
- **A render draws the seam of a full cylinder, cone or torus**, which is no boundary between
  two faces (eval 181). Two half faces give two such lines in opposite views (practice 3).
- **A third of the lines inside the views are not edges of our solid** (32 % of 762 000 px).
  Under them the truth has a depth step for 52 % (our surface in front for 93 % of those), a
  smooth or coplanar boundary for 42 % (a pure split for 15 % of all), nothing for 4 % (our eval
  renderer's own lines).
- `lines.py` grades (`tools/check_lines.py`): pieces called "below" have excess under them for
  16 876 of 16 992 px; "level" is a pure split for 51 % of its length; 28 % stays "unseen".
- **A line between two adjacent facing regions is a depth step** (175 of 179 pairs); where our
  solid has the pair coplanar it is wrong for 18 of 22. It does not say which side is too high.
- **A floor shows as a line in a perpendicular view only if the recess is open to that side**
  (eval 38: 8 of 11 wrong regions; 0 of 16 blind pockets on 236, 110, 74).
- **The outline of a region must be taken on the middle of the lines around it**; the 2 to 3 px
  offset otherwise misses a 2.5 px tolerance once projected.
- **A floor seen past something nearer has that thing's outline**, so its outline lands on no
  line at the right depth while its tone fits (eval 74, 171, 236).
- Lines under our surface exist on 7 of the 24 parts (15 186 px; eval 3, a tyre, has a third:
  seams of a curved surface, not pockets). 3423 px are in a closed figure, 345 px end up with a
  depth that stands out.

## The views: tones

- **A tone is a face direction, not a depth.** The renders hold no depth cue (regressing the
  facing tone on depth changes nothing). Depth comes from where a face of known direction
  appears in the isometric view.
- **The tone is one function of the normal in the camera's frame, the same for every view and
  part, per renderer.** A 21 x 21 table of median tone per direction predicts unseen parts with
  an rms of 2.3 levels on eval (ground-truth normals) and a median of 2 on practice (our normals;
  4 on part 7, 24 on part 6). One distant light from above the camera, direction (+0.13, +0.70,
  +0.70) in image x, y and towards the camera. Roughly: eval `120 + 10 x + 50 y + 53 z`,
  practice `90 + 15 x + 76 y + 77 z`; the table is what fits.
- Facing tone: 178 in eval renders (one exact value), 182 to 185 in practice (single views 176
  to 186). Tone by tilt direction:

  | tilt | practice: right, up, left, down | eval: right, up, left, down |
  | --- | --- | --- | --- |
  | 5 to 15 degrees | 176, 186, 170, 164 | 178, 184, 176, 170 |
  | 15 to 30 | 167, 210, 155, 128 | 175, 194, 167, 148 |
  | 30 to 50 | 157, 202, 136, 90 | 168, 198, 154, 117 |
  | 50 to 75 | 134, 186, 107, 76 | 150, 187, 130, 112 |

  Tilting down loses about 2 levels per degree; tilting up first brightens, so the tone has two
  answers there. A dent and a bump look different in one view.
- **The facing tone does not prove a face is square to the camera.** Tilts of 5 to 30 degrees
  right or up, and some steep ones (eval 211: 72 degrees shows 177), have it; so does a large
  even area (practice 6: a fin's flank of 71000 px). What tells a level face: seen edge-on from
  a perpendicular view it is one straight stretch with a line along it (100 % for the true
  ones, 9 % for the false one).
- **The facing tone must be taken from even areas of the six views, not from pixels**: the
  commonest colour is wrong on round parts and on views filled by a slanted face (eval 171,
  practice 9, whose "two tones" were its banks).
- **Measuring a side's isometric tone on our own faces goes wrong** where the solid has much
  excess (eval 51), where a "facing" region slopes gently (eval 171) and where a slanted face
  fills the view. `shading.py` measures it two ways and drops the one equal to another side's.
- **The real renders light a flat face unevenly**, up to 8 levels across the image; a plane
  plus a bowl around the centre fitted per area leaves 1 level. The eval renders do not.
- **The eval renders are flat-shaded per triangle**, the real ones smooth.
- **Which renderer made a case can be read off the solid**: the median misfit of a table is 1.0
  with the right one and 11 to 15 with the other.
- **Areas between lines can be classified by tone alone** (`regions.py`): facing, slanted or
  curved is right for 98.0 % of the area (facing 99.9 %, curved 98.6 %, slanted 68 %: a third
  is called facing and no threshold fixes it).
- **The isometric view settles facing against slanted only as a check of the solid's own face**
  (`normals.py`): solving a normal freely from two tones has several solutions and reads other
  faces where the depth is wrong. Asking whether both tones fit the solid's normal (5 levels)
  confirms 227 of 435 flat areas, all 54 truly slanted ones among them (slanted area right 68 %
  to 88 %). It finds no slanted face the solid lacks.
- **That confirmation tests direction, not depth**: a face far too high still lands on a
  parallel face of the part. As a guard in shading it blocked three right cuts on eval 38.
- **How much of a part's flat area is confirmed follows how right the solid is** (practice: 100 %
  on 1, 2, 3; 26 % on 6), and where the practice tones miss the table the solid is wrong (rms 4
  to 13 on good parts, 18 to 42 on 5, 6, 7, 9). `tools/check_views.py` uses this.
- **The table confirms a proposed face but does not measure its angle to 10 degrees**: right
  proposals of `curved.py` sit 0.5 to 3 levels from the table, wrong ones 20 to 40; but on eval
  249 the true cone (53.9 degrees) is 9 levels off and a wrong one (43.9) 1.5.
- **Comparing an area's tones with the solid's surface does not say the solid is right there**:
  a cone on the lit side has the facing tone to within 1 to 10 levels (practice 6).

## Practice parts

| part | what | box mm | faces | holes | note |
| --- | --- | --- | --- | --- | --- |
| 1 | retainer bracket | 19 x 19 x 51 | 17 | 2 | blind cylindrical recess along X |
| 2 | poppet valve | 53 x 86 x 53 | 12 | 0 | turned; a torus trough (R15.25) in the dish that no silhouette shows |
| 3 | flanged bushing | 22 x 132 x 132 | 9 | 1 | bore is two half faces |
| 4 | three-throw crankshaft | 212 x 197 x 565 | 178 | 0 | many fillets |
| 5 | cylinder head | 508 x 86 x 165 | 172 | 33 | 15 through-passages; dark openings and scallops in the bottom view that we have flat |
| 6 | finned air-filter cone | 140 x 140 x 137 | 122 | 1 | 18 fins, 72 spline faces; a shell a few mm thick |
| 7 | housing | 162 x 95 x 153 | 628 | 20 | deep open cavity (89000 px of the top view) that ours fills |
| 8 | hydraulic pump body | 80 x 128 x 40 | 306 | 19 | figure-eight window; 24 blind holes listed as 12 spans |
| 9 | V6 engine block | 508 x 256 x 512 | 371 | 23 | two inclined banks; crankcase hollow behind the bores |

Tiers by our rule (up to 30 faces simple, up to 200 medium): parts 1 to 3, 4 to 6, 7 to 9.

- **Practice 6.** As a body of revolution its outline holds 3.27 times the true volume; the
  flange alone holds more than the whole part. The bottom view is a closed face with one blind
  bore, so no view shows the hollow. About half of its 122 faces are outside; 36 B-spline faces
  are left for an inner surface. Intersecting our solid with itself turned by 90 degrees (on
  the brief's word) gave a wrong solid; a repeat angle must be read from the views.
- **Practice 8.** After the blind holes the excess (7.7 cm3) is the flange lobes around the
  bolt holes: in the part a lobe is a boss only at the top and bottom, about 7 mm each, with no
  lobe between; ours is a prism over the full height, since a silhouette is the union over the
  depth. Their profiles are lines inside the side and isometric views. The rest can be hole
  depth (up to 3.7 cm3). The isometric silhouette takes only 0.8 % off, under the minimum gain.
- **Practice 7 is as thin a shell as practice 6 by volume**, but its cavity is open and seen.

## The extended practice set

- `artifacts/extended-practice/inputs/test_case_10` to `test_case_20` (2026-11-13, from the organisers'
  `extended-practice.zip`): 11 parts in the format of the nine practice parts;
  `artifacts/extended-practice/index.csv` has their names (camshaft, camshaft bushing, two belt wheels, intake
  manifold, oil pan, side cover, valve cover, rocking arm spring, turbo shell, tubing spider). No ground truth.
  The zip held 20 parts; the other 9 are byte for byte the practice parts 1 to 9 and were left out. They
  must not lie in `artifacts/inputs`: pytest wants a `test_case_<n>.stp` in `artifacts/outputs` for every
  input there, so with them inside (017b8a1) CI failed and nothing was submitted; they were moved out again
  the same night. Run them with `--input_dir artifacts/extended-practice/inputs --output_dir <dir>`.
- Its README calls `facts.json` "practice set only, for debugging" and lists only the images, `context.png`
  and `description.md` as inputs for hidden parts (`open-questions.md`).

## The eval set

- 266 parts (100 simple, 100 medium, 66 complex by face count; 113 ABC, 153 Fusion 360), built
  by `tools/dataset/part_worker.py` with pyvista in the organisers' format. Each case has a
  `source.json`. Rebuilding needs `cadquery`, `pyvista` and 24 GB of raw downloads.
- It cannot measure: anything read from a brief, blind holes listed as spans, enclosed voids
  (no part has one), `context.png`.
- **Eval 99** is a cage of spline tubes (2 % of its box). **It does not give the same solid
  twice** under load (0.250 and 0.406): compare only on one cached solid. **Eval 51** has walls
  of 1 to 2 px; the fitted outline cuts them through and the traced fallback gives its faces.
  Together they are 52 % of the sample's lost IoU; parts filling under 5 % of their box are 6
  of 266 and no practice part.
- `--iou` samples 1500 points. A boolean with the ground truth is exact but fails on some
  complex parts (eval 3, 171).

## Which model drives the agents (2026-11-13, practice set, for the slides)

All runs: the image run exactly as the organisers run it (`--user`, inputs read-only, `team-d.env`), this
16-core GCP box, local score of `measure.py` (from f0f67de: with edge agreement; earlier runs used the old
score, which reads higher, so compare only within one score).

| model | mode, budget | result |
| --- | --- | --- |
| none (pipeline only) | parallel, 50 min | 945 s: OpenAI credit gone, every agent failed 30 times; mean 0.906 (1 0.988, 2 0.971, 3 0.986, 4 0.910, 5 0.913, 6 0.859, 7 0.773, 8 0.887, 9 0.863). The run stays valid and ends early. |
| gpt-6.1-terra | pipeline first, 30 min | old score, mean 0.980 |
| gpt-6-astra | pipeline first, 30 min | old score, mean 0.987; its outputs were committed (leaderboard: see `state.md`) |
| claude-sonnet-5-5 | parallel, stopped at 13 min | agents beat the pipeline on no part (mean 0.906). It ends its session after about 45 s ("Already good (0.856); I'll stop here … I made no changes"), is restarted, ends again: 14 to 16 starts in 13 minutes on parts 1 to 5. |
| claude-opus-5-5 | parallel, 50 min, plus the rule "do not end the session early" (2c048aa) | at 13 min mean 0.940, agent's solid on all 9 (4 0.955, 5 0.969, 6 0.958, 8 0.928, 9 0.882); one session each on the hard parts; ends sessions only on parts 1 and 3, saying the part matches the brief exactly. Final numbers: `state.md`. |

- The Claude route (`feat/claude-agents`): `OPENAI_BASE_URL=https://api.anthropic.com` with a Claude key in
  `OPENAI_API_KEY` and the model in `OPENAI_MODEL`, or `ANTHROPIC_API_KEY`; the harness's `anthropic-messages`
  route. Anthropic accepts the harness's thinking settings for Sonnet 5.5 and Opus 5.5.
- How persistent a model is matters more than how fast it is: the harness restarts an agent that ends below
  `HARNESS_GOOD_ENOUGH`, but at most 30 times a case, so a model that quits early burns its starts and leaves
  the pipeline's file.

## Where the loss is, and what did not work

Research round of 2026-11-12: scratch prototypes on cached solids, in
`runs/_research_scratch/`; plan and model experiment in `docs/plan-pipeline.md`.

- Share of lost IoU on the sample: eval 51 and 99 52 %; blind flat-floored pockets open to one
  direction 25 %; recesses open to two directions 12 %; fillets, chamfers, outline tolerance
  8 %; under-cut 3 %; enclosed cavities 1 %.
- Outside 51 and 99 the excess lies under flat floors square to a view: with every facing
  region at its true depth eval 38, 236, 110, 74 reach 0.98 to 0.99. Excess under facing areas
  11.4 part volumes, slanted 1.9, curved 6.3, under the lines 8.1 (summed over parts and views).
- **A cut raises IoU exactly when the share of true excess in it exceeds r / (1 + r)**, r being
  our volume over the true one: 0.51 at 1.05, 0.67 at 2, 0.79 at 3.7. So large structural cuts
  first.
- **The verifier prefers whatever brings the volume closer** (its score is multiplied by the
  volume ratio) and **drops for a right cut whose neighbours are still uncut** (the walls left
  standing are edges no view draws: practice 6, 0.227 to 0.206).
- **The score rule passes most wrong arbitrary cuts** (59 of 60 good and 175 of 307 bad column
  cuts); a per-pixel rule over the 8 views passes 55 and 30. On the cuts the steps themselves
  propose the score rule kept no wrong one. The per-pixel rule counts the walls of an unfinished
  recess against a right cut (eval 236), and no rule can choose a depth for a floor no view sees.
- Tried and not worth building:
  - depths from the briefs (no such numbers) or from tone (no depth cue);
  - removing the deepest material until the volume fits: eval 38 0.643 to 0.468. The true
    volume gives a depth only when all excess is known to lie under the chosen regions;
  - a slanted plane placed by its outline alone, three free numbers: false cut on eval 74;
  - cuts from coplanar pairs: +0.063 on the parts they were developed on, +0.004 outside;
  - symmetry (`solid AND mirror`): 0.8712 to 0.8715; repeated figures do not share a depth;
  - a second level-face pass after pockets: no change;
  - work aimed at eval 51 and 99.
- Surface metrics (Chamfer, F1 at 1 % of the diagonal) agree with IoU on removing outside
  excess, except eval 38. A closed void with the wall 2 mm off scores worse than the filled part
  under F1 and better under IoU.
- **A model proposing typed cuts looked large on practice 5, 7, 9 and is unverified**: one cut
  carries most of it (practice 7 top pocket, practice 9 crankcase), the proposers were told the
  excess, and there is no ground truth.

## How the code behaves

Geometry (OpenCascade):

- **Booleans with curved faces can return a wrong solid without saying so**, differently per
  platform (practice 6: 2 to 5 % too much on ARM, empty on amd64). A fuzzy tolerance does not
  help. Fitting fillets exactly tangent and lines exactly onto box faces did; hence the
  projection check after every cut.
- **A solid can be valid in memory and invalid in the file** (practice 4: a cone of half angle
  88.66 degrees passes `BRepCheck_Analyzer` until the `.stp` is read back).
- **`vh.tidy` changes the solid it is given**; only its return value is right. Hand it a
  `BRepBuilderAPI_Copy` if the original is used again. `split.py` and `tools/eval_hull.py` do
  not.
- **A triangulation stays on the shape it was made for**: a finer `verify.tessellate` changes
  what every later `verify.draw` sees. Take finer meshes from a copy.
- **The order of the silhouette cuts decides their time, not their result** (eval 99: isometric
  cut last 178 s, first on the box 0.4 s).
- A fitted loop can cross itself; `trace_outlines` keeps the traced polygon for such loops.
- **Most thin faces are not two views disagreeing about one plane**: they are corner patches
  where curved outlines of two views meet, and the polylines of eval 51 and 99 (95 % of them).
  Count thin faces by width, 2 x area / perimeter; by area, real small holes count.
- `BRepFeat_SplitShape`: pieces that meet must go in as one wire; one edge with a loose end
  drops all edges added with it; a figure must close or reach the face's border; the sweep also
  meets a cylinder's far side. `ShapeUpgrade_UnifySameDomain` undoes every split.
  `splitter.Modified(face)` gives the new faces, the same objects as in the result.

Pipeline:

- Straight cuts: the two opposite views are mirrored onto each other; a pixel is cut only if
  both show background.
- `outline_fit.py`: the traced outline is split at the corners of a 1.2 px polygon; runs within
  1 px of a circle sweeping 12 degrees become arcs; lines drifting under 1 px become exactly
  horizontal or vertical.
- **`Cutter.run(operation, shape, argument, timeout)`** runs a boolean in a spawned child. The
  operation must be a module-level function, the argument picklable.
- **A new later step** is a module with `apply(agent, solid, case_dir, views)`, added to
  `LATER_STEPS` and `STEP_NAMES` in `hull_agent.py` and to `STEPS` in `tools/eval_hull.py`.
- **A step takes views and drawings from `agent.evidence(case_dir, dims)`**: `.observed`,
  `.drawings(solid)`, `.lines(solid)`, `.regions()`, `.rows(solid)`. They are found by the
  solid's identity (a copied or tidied solid is another solid) and must not be written into.
- Verifier: `observe(case_dir)` gives mask, lines and mm per pixel of each view;
  `draw(mesh, view, dims, mm_per_px)` depth, face and normal per pixel;
  `check(solid, case_dir, observed).score`. Most of a check's time is `verify.draw` (edges,
  normals), not the boolean (0.03 s) or the triangles.
- A hole of ours can show past the edge of a face, as a sliver of lower level in the
  neighbouring face, when the view draws the hole elsewhere (eval 123).
- Only a case whose box cannot be read (no `facts.json`, no Envelope line) ends without a file.
- A case's trace can be entered more than once with the same `CaseTrace` and `more` set;
  `tracer.case(name)` a second time would restart the numbering. Tracing never raises, writes to
  `runs/` (or `/tmp`), and is off with `TRACE=0`. `role="stage"` on an attached `.stp` makes it
  a stop.

Dashboard:

- It can start runs; those write to `runs/<run>/outputs`. Only `scripts/main.py` without
  arguments overwrites `artifacts/outputs`.
- The overlap is measured in the browser (`step-worker.js`): 40 000 points, ray test through
  the tessellation.
- The 3D diff is drawn from depth textures, not computed by a boolean. In three.js two materials
  with the same `onBeforeCompile` source share a program unless `customProgramCacheKey` differs.
- The section needs no cap geometry: clipped pixels showing the inside of the skin are the cut
  face. That needs outward winding; occt-import-js returns some solids wound inwards, so the
  worker flips a mesh with negative signed volume.
- **A pinch counts as a through-hole** (2026-11-12). The genus from the topology (V - E + F - inner loops) rises
  where two bodies touch in one edge or one point without a hole being there. The rounds step leaves such points:
  a boss cut round that is tangent to a flat side of the hull (practice 4: two bosses, count 2, then 1 after the
  faces are merged, for a part with 0). Healing (ShapeFix, sewing, a fuzzy boolean) does not remove them; cutting
  0.2 mm away at the point does. On the leaderboard the pipeline without this scored 0.839 with practice 4, 5 and
  8 at 1, 6 and 9 holes for 0, 15 and 11 (fdcc8c6). What the organisers' count makes of a pinch is not known.
- **Practice 8 keeps 9 of 11 holes**: the two 4 mm holes along z that are missing touch the 6 mm cross holes;
  cut as listed they leave pinched edges that are not straight, cut 0.05 mm wider or narrower the result is not a
  valid solid. The step's own count on the merged solid says 11 there (4 pinches), the file read back says 9.
- **Openings that are not cylinders are in no list of `facts.json`** (2026-11-12): practice 5's six ports are 5
  degree cones (`cones_half_angle_deg`), about half of that part's excess. A straight view draws their mouths.
  Without the wall, depth and nesting conditions the same rule cut into the part on eval 9 (a groove around a boss:
  73 to 96 % of the cut was material), 33 and 189 (chamfers) (`tools/seen_mouths/find.py` prints the share against
  the ground truth).
- **A pretrained single-image depth model does not know the depths we get wrong** (Depth Anything V2 Large, 24 eval
  parts, six straight views, `tools/depth_spike/`): fitted to our own solid its error on the pixels where we are
  more than 2 % off is 19.7 % of the part's mean extent against our 19.0 %, fitted to the truth 12.6 %; on the
  pixels where we are right it adds 5 %. 144 views take 16 s.
- **The served model's step as it is on `main`** (Gemma 4 31B, 24 eval parts, four-term local score): 0.911 to
  0.913, 313 s to 694 s; 171 +0.035, 74 +0.026, 110 -0.010, 38 -0.005.

- **The later steps stop for time only between their own attempts** (2026-11-12, `uzum`). Level
  faces checked `agent.out_of_time()` once per view, not inside its one-cut-at-a-time loop, and
  asked the verifier for the base solid's outline again on every attempt; on practice 7 after the
  cavity cut it ran 1481 s alone. Both fixed. Shading and level faces have no clock of their own on
  purpose (same solid on a slower machine); only the case's share of the run stops them.
- **Practice 6 hides an enclosed void, but not a thin shell** (2026-11-12, `uzum`, on `main`'s
  output). The brief says "a single solid" and `facts.json` gives genus 0, yet the flange alone
  (Ø140, 27.5 mm with R10 rounds) holds about as much as the true 377.9 cm3, so the part must be
  hollow. Our solid: 1042.6 cm3, 1377 cm2 of area against the true 1697.5 cm2. A shell of even
  walls would have about twice the outer area; 1697 means a compact inner void of a few hundred
  cm2. No view shows where it is or its shape. OpenCascade's inward `MakeThickSolidByJoin` on our
  finned solid gives an invalid solid at 4 mm and fails at 6 mm (about 40 s each).
- **The run needs about 2 GB a case, and a killed process leaves cases waiting for good** (2026-11-12, the image run as
  the organisers run it, on the GPU box). 9 practice parts at 4 at a time: 3 min 57 s, peak 7.4 GB, outputs equal
  to the committed ones; with 2 cores and 8 GB 6 min 59 s (practice 7 a hair different: 1.460 for 1.458). With a
  4 GB limit a process was killed for memory and after 20 minutes four cases were still their bounding boxes, the
  container still running. `scripts/main.py` now takes the number of cases at once from the memory it may use
  (cgroup limit or free memory, 2.5 GB a case) and runs cases whose worker died again one at a time.
- **Islands**: of 29 flat areas standing above everything around them in our solids of the 24 eval parts, 14 are in
  the part and 15 are not (`tools` none; measured in the session). The isometric view tells them apart: share of
  the top outline on its lines 0.62 to 1.00 for the real ones, 0.09 to 0.60 for the others.
