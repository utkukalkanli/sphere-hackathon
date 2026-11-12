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

## `facts.json`

Exact, measured from the ground truth. **Whether the hidden parts have it is in doubt**: the
README of the organisers' extended practice set (2026-11-12) calls it "practice set only, for
debugging" and lists the inputs as `images/`, `context.png` and `description.md`, "nothing else";
this repo's README lists it among the inputs (`open-questions.md`).

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

- **Extended practice set** (organisers' `extended-practice.zip`, 2026-11-12): 20 named parts. Nine
  are byte for byte our parts 1 to 9 (1 camshaft_retainer, 2 engine_valve, 3 crankshaft_bushing,
  4 crankshaft, 5 engine_silinder_head, 6 engine_air_filter, 7 practice_piece,
  8 hydraulic_pump_main_body, 9 engine_block); the other eleven are in `artifacts/inputs` as 10 to
  20, in the zip's alphabetical order. No ground truth, no scorer in the zip. Only 20 lacks
  `context.png`.

  | part | name in the zip | box mm | faces |
  | --- | --- | --- | --- |
  | 10 | engine_a__camshaft | 35 x 38 x 463 | 64 |
  | 11 | engine_a__camshaft_bushing | 19 x 25 x 25 | 6 |
  | 12 | engine_a__engine_belt_wheel | 60 x 60 x 41 | 273 |
  | 13 | engine_a__engine_belt_wheel_pt2 | 166 x 166 x 46 | 703 |
  | 14 | engine_a__engine_intake_manifold | 995 x 281 x 410 | 350 |
  | 15 | engine_a__engine_oil_pan | 508 x 127 x 330 | 265 |
  | 16 | engine_a__engine_side_cover | 478 x 319 x 52 | 157 |
  | 17 | engine_a__engine_valve_cover | 64 x 165 x 508 | 323 |
  | 18 | engine_a__rocking_arm_spring | 27 x 30 x 27 | 16 |
  | 19 | engine_a__turbo_shell | 82 x 156 x 175 | 79 |
  | 20 | tubing_spider | 328 x 170 x 333 | 341 |

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

## Sources derived from the views

`sources.py`, graded by `tools/check_sources.py` on the 24 eval parts (2026-11-12). The research
scripts behind them are in `runs/_research_scratch/sources/`.

- **Upscaling adds nothing; lines are missed by the threshold, not by the resolution.** A line
  is 1 px wide and its darkness follows the face it lies on (practice 7: line 59 on a face of
  83). `verify.find_lines` finds 92.1 % of the true sharp edges inside the eval views; with the
  tone steps (`sources.steps`, jumps of 24 levels) 98.2 %, precision unchanged at 0.986. A
  relative threshold with hysteresis alone gave 94.0 %. Not measured on the organisers' renderer.
- **Under our excess the true surface is seen by no other camera for half of the columns, but
  most flat floors are seen in part.** Without eval 51 and 99 (cached solid of 2026-11-12
  03:45): 47 % of the excess columns have their true point in an isometric view, 17 % in a
  perpendicular view, 50 % in none (eval 38 97 %, 110 84 %). Per flat floor: 84 % of the excess
  lies under a floor of which an isometric camera sees at least 200 px; 16 % under floors nobody
  sees (eval 38's back channel).
- **Found edge pixels are not closed faces.** The share of true edge pixels found (98 %) says
  nothing about gaps, and one gap joins two faces. Per true face seen in a view (4670 on the 24
  parts, at least 150 px), the best overlap with one area: 0.9 or more for 35.5 % of the faces
  with the areas between line pixels (`regions.segment`, grown over the lines; 82.9 % by area,
  33 % under 0.5) and for 41.9 % with the faces of `drawing.partition` (88.3 % by area, 24 %
  under 0.5). What is still lost is small: faces a few pixels wide have no core and join a
  neighbour (eval 123's threads, 146's chamfer strips), and the eval renderer's flat shading
  cuts curved faces into strips (eval 147: 10 % either way), which the organisers' does not.
- **Where the excess is after the drawing, plane faces and recesses** (research script `e15.py`,
  commit `1db8ab7`, the 24 parts' final solids, straight views, in true volumes summed over the
  views). 20.3 in all, 19.5 of it in eval 51 and 99. The other 22 parts hold 0.82 (1.50 before
  these steps), by what the picture says of the face over it and what our solid has there:
  curved face 39.5 % (eval 3 0.16, 171 0.11); facing face where our solid is one level with steps
  all round, so a recess of ours that is too shallow, 36.2 % (110 0.16, 74 0.06, 236, 171; the
  true floor flat and square for all of it); facing, our solid not flat there 7.7 % (171);
  facing, level across a border without a T 5.8 % (171), with a T 3.7 % (236); slanted flat face
  3.5 %; faces under 150 px 2.0 %; facing over several levels of ours 1.5 %.
- **Eval 51's excess lies under one face.** Its walls are 1 to 2 px and the drawing has the
  whole inside of the tray as one facing face (247 918 px of the top view); the recess step
  takes it, with a T, and finds no depth at which the views fit better.
- **How well a face is separated follows its width** (research script `e12.py`, 22 eval parts;
  width = 2 x area / perimeter; lost = best overlap under 0.5): under 2 px 99 % lost, 2 to 4 px
  53 %, 4 to 8 px 18 %, 8 to 16 px 5 %, wider 11 %. Faces of 4 to 8 px are rarely lost and
  rarely reach 0.9: a pixel of border is a sixth of such a face.
- **Dividing the view enlarged 2, 3 or 4 times does not bring the narrow faces back** (2026-11-12;
  edges thinned to their crests, cores grown over the enlarged picture): faces of 2 to 4 px lost
  71 to 76 % against 53 %, all faces of 150 px or more separated 0.47 to 0.54 against 0.55, at
  0.55 to 2.4 s a view against 0.17. Two lines a pixel wide and 2 to 3 px apart leave nothing of
  the face between them in the picture. Not kept (`runs/_research_scratch/sources/
  drawing_fine_attempt.py`).
- **Lines too faint for one pixel, found over their length, change nothing** (`drawing.along`,
  `HULL_FAINT_ALONG=1`: the darkness averaged over 21 px in 16 directions). True sharp edges
  found 98.1 to 98.7 %, but edge pixels on a true edge 98.3 to 94.3 %: the eval renderer's flat
  shading makes dark slivers on spline faces (eval 145, 99, 3). On practice views it adds a few
  hundred pixels a view; the line pixels found before already cover the faint lines there.
  Pipeline on the 24 parts 0.9076 to 0.9075.
- **A circle seen at an angle is neither a line nor an arc**: fitted to 0.6 px it comes out as
  chords of about 30 px. Pieces that run on turning the same way (1.5 to 30 degrees) are one
  smooth curve.
- **An isometric view gives axis faces by tone alone.** A flat face within 4 levels of the
  table's tone for one of the three sides the camera sees is that side: 55 % of the iso pixels on
  the drawing's faces (48 % on the areas between line pixels), 91.9 % of them right.
- **Such a face gets its plane without our solid** (`sources._level`): on the drawing's faces 258
  get a level, 184 within 3 px (98.3 % by area); "sure" (stands out and at least 10 000 px) 72
  of 76, 99.0 % by area (73 of 75 on the areas between line pixels). What fails: small faces,
  and a floor seen past the rim of its pocket, which lands on the rim's level (eval 110: five
  pockets 100 px off). Taking the peak farthest from the camera made it worse. On the faces
  where our solid has excess the levels that stand out were right for 16 of 53 (measured on the
  earlier areas).
- **Most inner borders of a view are edges where one face hides another, not where two meet**
  (research script `e9.py`; truth: the two faces' true depths, carried on to the border, differ
  by more than 3 px). All borders: 59 % in the isometric views, 48 % in the straight ones.
  Borders of 15 px or more between two flat faces: 68 % isometric, 89 % straight (96 % of the
  length).
- **A T (two borders running straight through a meeting point, a third ending there) marks a
  hiding edge only fairly well** (`drawing.occlusions`): between flat faces in the isometric
  views 110 of 136 marked borders truly hide (81 % against the 68 % of all), the face in front
  right for 94; it finds 68 % of the hiding length, and the unmarked rest still hides on half.
  Over all borders 68 % right. In the straight views 56 of 57, where nearly everything hides.
- **Our solid across a border** (research script `e11.py`, 22 eval parts, borders of 15 px or more
  between flat faces, cached end-of-pipeline solid). Straight views: where our solid has a jump
  the part has one too (85 of 86, the same face in front for 85 %); where our solid is level and
  both sides have the facing tone the part has a step for 40 of 46, on average 100 px under our
  surface; with a T, 13 of 13 are steps and the deeper side is named right for 12. Isometric
  views: a jump of ours is a true step for 89 %; level and of one tone, a step for 13 of 32.
- **A wrong level of an isometric face is nearer the camera than the true one** (eval 110: every
  pocket floor lands 4 mm high, on the edge of its own pocket; that edge belongs to no flat face
  beside it). Leaving out the levels of neighbouring parallel faces changed nothing; taking the
  farthest of the peaks within 90 % of the best made one more sure level right (73 of 76); at 75 %
  and 60 % it loses others.
- **A plane cannot be carried to a neighbouring axis face across one border** (research script
  `e8.py`, our levels as the start): 9 of 27 faces right. The border of two faces of different
  axes looks the same whether they meet there or the far one lies behind (a floor and a back
  wall against a top face's far edge and a wall behind it).
- **A slanted flat face with borders to two faces of known plane gets its plane from the points
  of those borders**, kept if they are planar to 1.5 px and the table's tone for that normal is
  the face's (5 levels): 8 of 8 right, but only 2.3 % of the iso pixels on the 24 parts (eval 1,
  109, 110, 235). Not in `src/`.
- **Of the isometric pixels of the 24 parts 58 % are flat faces, 50 % axis faces**; the rest is
  curved (and in the eval renders cut into strips). Our levels that stand out cover 39 %.
- **The renderer can be told from the isometric views alone**: the table whose three side tones
  explain more of their flat area (`sources.choose_table`). With it the grades above are the
  same as with the table chosen on the ground truth.
- **A monocular depth network is a coarse prior on the isometric views and useless on the
  straight ones.** Depth Anything V2 after an affine fit per view: Small (0.2 s an image, laptop
  CPU) median error 3.7 % of the depth range in the front iso view, 8.1 % in the back one, 64 %
  and 43 % of the faces within 5 %; Large (1.6 s) 3.3 %, 74 %. Top view: correlation 0.18 (a
  straight view of a vertical-walled pocket shows no wall).
- **A face of an isometric view is not always seen by the straight view that looks at its
  side** (practice 9: the inner wall of the crankcase, x = 19.8 facing +X, is seen through the
  open front; the right view shows the block's closed end there). Its level came out right and
  "sure", and the area it lands in is another face 488 mm in front of it. Only the volume guard
  stopped that cut.
- **On a face seen nearly edge-on a pixel across is many pixels of depth** (eval 1: a face at 81
  degrees, 6.6 px a pixel). A drop measured along the view there is noise of the edge's position.

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
- **The dashboard's waits were one worker doing everything in the order asked**: the ground
  truth, then every step of the run, and only then the solid on screen and its overlap. It shows
  on parts whose files take seconds to read (eval 99's truth is 11 MB), not on the practice set.
- `verify.camera` is asked for 77 000 times while the sources of practice 7 are read
  (`sources._scan`); it now works a view's frame out once and hands out copies (uncommitted,
  2026-11-12). Under the profiler it looked like 4.6 of 14 s; timed plainly the source pictures
  of practice 7 go from 13.7 to 12.0 s, practice 1 from 5.1 to 4.8 s, the same bytes.
- **Most of a drawing's time is fitting lines and arcs to thousands of short runs**, and there
  the cost is the call, not the arithmetic. `drawing._line_through` and `_circle_through` now
  solve in closed form instead of `svd` and `lstsq` (uncommitted, 2026-11-12;
  `outline_fit.fit_circle`, which the silhouette cuts use, is untouched and is the fallback for
  points nearly on one line). The eight drawings, best of 3, alternating: practice 7 4.67 to
  3.29 s, practice 9 2.92 to 2.19 s, practice 1 1.09 to 0.99 s. On the 9 practice parts and the
  24 eval parts every view has the same faces, strokes and borders (79 580 strokes), no
  coordinate, centre or radius more than 1.7e-8 px away; the source pictures of practice 1 and
  7 are the same bytes. What is left: the same fits (2.6 of 4.9 s under the profiler),
  `lines.thin`, the partition and the borders, about 0.4 s each on practice 7.
- The 3D diff is drawn from depth textures, not computed by a boolean. In three.js two materials
  with the same `onBeforeCompile` source share a program unless `customProgramCacheKey` differs.
- The section needs no cap geometry: clipped pixels showing the inside of the skin are the cut
  face. That needs outward winding; occt-import-js returns some solids wound inwards, so the
  worker flips a mesh with negative signed volume.
