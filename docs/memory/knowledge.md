# What we know about the task and the code

Facts that took reading or measuring to find out. They change rarely; correct them when they do.

## The inputs

**`facts.json` is exact and is given for the hidden parts too.** It was measured from the ground
truth. What it holds:

| field | what it gives | what it does not give |
| --- | --- | --- |
| `bounding_box_mm` | x, y, z size | |
| `volume_mm3`, `surface_area_mm2` | the true values: a target to check any candidate solid against | |
| `faces`, `edges`, `face_types`, `through_holes_genus` | counts per surface type; number of through-passages | |
| `cylindrical_holes_and_bores` | full concave cylinders: diameter, axis, count, centres (midpoint of the axis) | `lengths` is the set of distinct lengths in the group, not one per centre |
| `cylindrical_shafts_and_bosses` | full convex cylinders, same layout | |
| `partial_cylinders_arcs` | diameter, concave or convex, face count | no position, no axis |
| `fillets_rounds_from_cylinders`, `fillets_from_tori_minor_radii`, `cones_half_angle_deg` | radii and angles with counts | no position |

- An axis is `"X"`, `"Y"`, `"Z"` or a vector written as text. Positions are in mm from the
  bounding-box min corner, in the frame of the views.
- A cylinder counts as full when its faces sweep at least 98 % of a turn. Counterbores, grooves and
  stepped bores appear as several entries on the same axis.

**`description.md` in the practice set is written prose with information that is not in
`facts.json`**: plate thicknesses, where a pocket or rib sits and how deep it is, which holes are
blind, symmetry planes. Example, practice part 1: "plate 14.0 mm thick in Y, rear rib protrudes 5.0
mm, recess Ø25.4 with axis parallel to X centred at Z = 25.4, Y = 7.0". It always has an
`**Envelope:** X x Y x Z mm` line.

**The views** are 1024 px, orthographic, white background, the part in flat tan shading with dark
lines on the face boundaries. Pockets and cavities are visible as shading and edges, especially in
the two isometric views.

- The part is centred in the image. Half the image equals the framed extent times 1.04.
- **A tone is a face direction, not a depth** (measured 2026-11-11). In a straight view every flat
  face square to the camera has the same tone whatever its depth (practice: 182 +-5, with a slow
  gradient across the image; eval renders: one exact value). Slanted faces are darker (practice 8
  front: 154 and 130). An isometric view gives the three sides it sees three tones (practice:
  194 / 102 / 82), so there a tone says which way a face looks. Depth comes from where a face of a
  known direction appears in the isometric view. The real renderer and our eval renderer light
  differently, so tones must be measured per view on the solid, never hard-coded.
- **Measuring a tone on the solid's faces goes wrong in three ways** (all found 2026-11-11, each
  made a false cut): (1) where the solid has far more material than the part, most of its faces of a
  direction are not faces of the part, and the commonest tone under them belongs to another side
  (eval 51: 117 instead of 192 for the top in the isometric view); (2) a region taken for a face
  square to the view can be a gently sloping one, which the isometric view shows in another side's
  tone (eval 171); (3) in one straight view the commonest tone can be that of a sloping face that
  fills the view (eval 171 front: 180, the other five views 176 and 177). What `shading.py` does
  now: the facing tone is the middle one of the six straight views; the isometric tone of a side is
  measured two ways (all faces of the solid, and only faces whose outline lands on isometric lines)
  and where they disagree the one equal to another side's settled tone is dropped, because the
  three sides of one isometric view have three tones.
- **The facing tone is nearly one value across the six straight views on the real renders too**
  (measured 2026-11-12 on the 9 practice parts, on the solid's facing faces): 176 to 186 per view,
  the middle of the six 182 to 185. Practice 9 has no 18-level gap there; that gap is between the
  commonest colours of the views, two of which show mostly the slanted banks. Single views do sit
  up to 6 levels off the middle (practice 7 right: 176 against 182 +-3), so `shading.facing_tone`
  can miss the facing faces of such a view. That errs on the side of not cutting.
- **A gently sloping face has almost the tone of a face square to the view** (Lambert: 15 degrees
  is 3 % darker), so tone cannot tell them apart. What does: the solid usually has the slope right
  already from the side silhouette, and then its outline lands on the isometric lines as it is.
  Such a face is left alone (`shading.confirmed`).
- **In the real renders the tone depends on which way a face tilts, not only on how much**
  (measured 2026-11-12 on the dish of practice 2, back view, whose shape is known from the brief:
  a torus of minor radius 15.25 around the stem). Facing tone 186. At 25 degrees of tilt the four
  tilt directions (right, down, left, up in the image) give 167, 125, 154, 230; at 5 degrees 179,
  170, 176, 184. Reversing the tilt reverses the pattern, so a dent and a bump look different in one
  view. So the Lambert figure in the bullet above ("15 degrees is 3 % darker") understates it for
  these renders: the light is not at the camera. `tone = 63 + 16 nx - 86 ny + 113 nz` (image axes,
  nz towards the camera) fits the dish with an rms of 6.6 levels, about 4 degrees; the rest is a
  highlight (230 is brighter than facing). One tone is still one equation for the two unknowns of a
  normal.
- **The tone is one function of the normal in the camera's frame, the same for every view and every
  part, in both renderers** (`tools/check_normals.py`, 2026-11-12). A table of the median tone per
  direction of the normal (21 x 21 cells), built from the other parts, predicts a part it has not
  seen: on the 24 eval parts (normals from the ground truth) with an rms of 2.3 grey levels, median
  0, and no view worse than 3.3; on the 9 practice parts (normals from our solid after steps 1 to 4,
  so wrong where the solid is) with a median of 2 levels, 1 to 2 on seven parts, 4 on part 7 and 24
  on part 6. The light moves with the camera and comes from above it in both: as one distant light,
  direction (x +0.13, y +0.70, z +0.70) with x to the right of the image, y up, z to the camera.
  Eval: `tone = 120 + 10 x + 50 y + 53 z`; practice: `90 + 15 x + 76 y + 77 z`. The linear form is
  only rough (rms 5.4 on eval); the table is what fits.
- **How much a tilt shows depends on its direction** (same run, tone by the table; facing is 184 in
  the practice renders, 178 in the eval renders):

  | tilt | practice: right, up, left, down | eval: right, up, left, down |
  | --- | --- | --- | --- |
  | 5 to 15 degrees | 176, 186, 170, 164 | 178, 184, 176, 170 |
  | 15 to 30 | 167, 210, 155, 128 | 175, 194, 167, 148 |
  | 30 to 50 | 157, 202, 136, 90 | 168, 198, 154, 117 |
  | 50 to 75 | 134, 186, 107, 76 | 150, 187, 130, 112 |

  A face tilting down (its normal turning towards the bottom of the image) loses about 2 levels per
  degree; tilting up it first gets brighter and then darker again, so the tone there has two answers.
- **Where the practice tone does not fit the table, our solid is wrong there**: the rms on the
  practice parts is 4 to 13 levels on parts 1, 2, 3, 4, 8 (volume within 10 % of the truth) and 18
  to 42 on parts 5, 6, 7, 9 (the ones with large excess). Not yet used.
- **Areas between the lines can be told apart by tone alone, except flat faces at some angles**
  (`src/agentic_cad/regions.py`, `tools/check_regions.py`, 24 eval parts, 1367 areas of at least
  250 px in the six straight views, truth from the ground truth's normals, 2026-11-12). Called
  facing (even tone, the facing one), slanted (even, another tone) or curved (uneven): right for
  98.0 % of the area and 91.3 % of the areas. By area: facing 99.9 % right, curved 98.6 %, slanted
  68 %. A third of the slanted area is called facing, and no threshold fixes that: other directions
  have the facing tone too (tilts of 5 to 30 degrees to the right or upwards, and some steep ones:
  eval 211 back at 72 degrees shows 177 against 178). It takes a second view of the same face.
- **The isometric view settles facing against slanted only as a check of the solid's own face**
  (`src/agentic_cad/normals.py`, `tools/check_regions.py`, 24 eval parts, our solid after steps 1 to
  4, 2026-11-12). The solid's surface under a flat area is projected into the isometric view that
  sees that side and the tone there is read. Solving the normal freely from the two tones went wrong
  in three ways: two equations have more than one solution (eval 145 top: 76 degrees for 67, 57
  degrees off in direction); where the solid is at the wrong depth the tone is read off another face
  (eval 38 top, the floor of the U-channel: "78 degrees" for a face square to the camera); and small
  areas land on a neighbour. Every normal it got right was also the solid's own normal there. So the
  test is: do both tones fit the normal the solid has (within 5 levels each)? Then the area is that
  face. Result: of 435 flat areas 227 are confirmed (90 % of the flat area); among them all 54
  truly slanted ones are called slanted (35 by tone alone), normal off by 0 degrees median and 1 at
  the 90th percentile. By area the slanted faces go from 68 % to 88 % right, all faces from 98.0 %
  to 98.9 %. It finds no slanted face the solid does not already have.
- **The isometric confirmation tests a face's direction, not its depth, so it must not stop the
  shading step from cutting** (2026-11-12, 24 eval parts, solid after steps 1 to 4). A face of the
  solid that is far too high still lands, in the isometric view, on some parallel face of the part
  and shows the right tone. Of 217 areas `normals.py` confirms, the solid is within 2 px of the
  truth under 200; `shading.confirmed`, which also wants the outline on lines, is right on 155 of
  166. Used as one more reason to leave a face alone it blocked three floors on eval 38 that the
  shading step lowers correctly (160 px proposed, the truth 164 px down): IoU 0.639 without it,
  0.610 with it. It changed nothing on the other parts, including the one wrong proposal it would
  have stopped (eval 146, which the step drops by itself).
- **How much of a part's flat area the isometric view confirms follows how right the solid is**
  (practice parts, same date): 100 % on parts 1, 2, 3 (volume within 2 % of the truth), 96 % on 5,
  86 % on 8, 61 to 62 % on 4 and 7, 47 % on 9, 26 % on 6. An area that is flat in the view and not
  confirmed is a place where the solid is wrong or hidden from the isometric camera. Not used yet.
- **The facing tone does not prove a face is square to the camera, even on a large even area**
  (practice 6, left view, 2026-11-12): the flank of a fin, 71000 px with the tone of a facing face
  to within 3 levels, is not one. Taken for one it was cut flat to the solid's lowest point, 30 mm
  into the cone, and the verifier accepted it. What tells a level face: seen from a camera at right
  angles it is one straight stretch, and the view draws a line (or the outline) along it. The flat
  triangles of practice 6 have that line along 100 % of their stretch; the false one along 9 %.
- **"The solid contains the part" gives depths without any other source** (2026-11-12). A level
  face is no higher than the solid's lowest point over its area; a flat face through a known edge is
  no steeper than the steepest plane through that edge inside the solid. Both cuts can only remove
  what is not part, as long as the area really is that kind of face. On practice 6 the flanks of the
  fins come out at 82 to 83 degrees and the planes touch the cone along the whole ridge (0.3 to 3 mm
  over 35 mm of radius).
- **The verifier's score drops for a right cut whose neighbours are still uncut** (practice 6: 14
  triangles and 15 flanks cut, volume 3.31 to 2.99, score 0.227 to 0.206). The walls left standing
  next to a cut are edges the views do not draw, so precision falls (front view 0.94 to 0.44), while
  recall rises in every view (0.61 to 0.74): more of the views' lines have become face boundaries.
- **The organisers' renderer draws no line at the bottom of a groove** (practice 6, top view,
  every ridge and valley of the lower half looked at pixel by pixel, 2026-11-12). Each ridge between
  two flanks has a line 20 to 80 levels darker than both; no valley has one, only the step from one
  flank's tone to the other's, which is as little as 2 levels (70 and 68). So two faces that meet
  in a concave crease can be one area, and no threshold separates them. `depth.py` solves such an
  area from the level edges it lies along instead (see `state.md`).
- **Comparing an area's tones with those the solid's surface would have does not say whether the
  solid is right there** (practice 6): under four of the flat triangles the solid is a cone whose
  tone, on the lit side, is the facing tone to within 1 to 10 levels. Used as a guard it kept those
  four from being levelled.
- **Lines between two dark faces are missed** (practice 6, top view: faces of tone 55 to 70, line
  30 to 45). `regions.segment` now also takes what a 5 px closing fills in by 8 levels and 15 % of
  the tone; that separates most flanks, not those in the darkest third of the view. An area made
  of two faces bends around a corner: `depth.py` skips areas under 90 % of their convex outline.
- **Eval part 99 does not give the same solid twice**: hull, holes and rounds scored 0.250 in one
  run and 0.406 in the next (volume 3.77 and 2.32 times the truth), with other sessions loading the
  machine. A before/after on that part means nothing unless both use one solid.
- **The facing tone has to be taken from even areas, not from pixels.** The commonest tone of a
  view, or the middle one of the six, is wrong on round parts, whose side views show no flat face
  (it called 3.4 % of the truly facing area slanted). The tone that most of the even area of the six
  views has got every facing area of the 24 parts right.
- **The real renders light a flat face unevenly**: up to about 8 levels across the image, smoothly
  (practice 1 right: 178 to 186 on one flat face; practice 2 front: 183 to 186). A plane plus a bowl
  around the image centre, fitted per area, leaves about 1 level. Without that, large flat faces of
  the practice parts count as curved (practice 1: 53 % of the area facing, 77 % with it). The eval
  renders have none of it.
- **Where a bright face meets a dark one the line between them is not found** (practice 1 front: the
  flat block and the dark start of the recess were one area). `find_lines` wants a line 28 levels
  darker than both sides. `regions.segment` also splits where neighbouring pixels differ by 24
  levels. In the eval renders that splits some curved faces into their triangles, which then look
  flat (complex tier 98.2 % to 95.6 % of the area right); the real renders are smooth.
- **Which renderer made a case can be read off the solid** (2026-11-12): the median difference
  between the views' tones and a tone table applied to our solid's faces is 1.0 with the right
  table and 11 to 15 with the other (eval 249: eval 1.0, organisers 14.6; practice 2: organisers
  1.3, eval 11.3). `tone.choose` takes the organisers' table unless another has under half its misfit.
- **The tone table confirms a proposed face but does not measure its angle to 10 degrees**
  (2026-11-12). Faces proposed rightly by `curved.py` sit 0.5 to 3 levels (median) from the tones
  the table gives them, the flat face they replace 8 to 22 away; the wrong proposals on practice 5
  were 20 to 40 away. But on eval 249 the true cone (half angle 53.9) is 9 levels off and a wrong
  one (43.9) 1.5: the ground truth normals there are 36 degrees from the camera and the render is 7
  to 16 levels darker than the table says for that direction on the lower half of the ring. Why is
  not known.
- **The verifier prefers whatever brings the volume closer**, since its score is multiplied by the
  volume ratio: between three cones on eval 249 its line terms were equal to three digits and it
  chose by volume alone. That is right only when the candidate accounts for the whole recess (the
  cone carried through to the axis), and wrong when part of the excess is elsewhere (the same cone
  stopped at the next line: it picked a steeper one that cut into the part, IoU 0.83 against 0.88).
- **The eval renders are flat-shaded per triangle** (`smooth_shading=False`, triangles up to 0.15
  rad apart), the real renders are smooth. A curved face has steps of tone in the eval renders.
- **Practice 2 after step 3 is exactly the revolved silhouette** (12095 mm3 against 12131 computed
  from the pixels; true 10947). All of the excess is one ring-shaped trough in the dish behind the
  rim: the R15.25 arc that the silhouette shows leaving the stem carries on below the rim's edge
  between radius 12.4 and 24.4 mm, 1.23 mm deep (about 1140 mm3 computed). No silhouette shows it;
  the back view shows it as shading, and `facts.json` has the radius (`fillets_from_tori_minor_radii`).
- **The outline of a region must be taken on the middle of the lines around it.** A region ends
  where the line begins; that offset of 2 to 3 px misses the 2.5 px tolerance once projected.
- **A floor seen past something nearer has that thing's outline, not its own**, so its outline lands
  on no line at the right depth, while its tone fits (eval 74, 171, 236). Lines cannot find these.
- **A slanted plane placed by its outline alone is not reliable** (2026-11-11). Trying every fall
  direction, slope and depth and keeping the plane whose outline lands on isometric lines gave a
  false cut on eval part 74: with three free numbers some plane always lands on lines. Hung on an
  edge of known depth, with only the slope free, it gave none on the 8 parts checked exactly.
- `tools/eval_hull.py --iou` samples 1500 points, so it cannot see a change under about 1 % of the
  volume. A boolean intersection with the ground truth is exact but fails on some complex parts
  (eval 3 and 171 gave nonsense).
- The solid drawn with `verify.draw` lands within 1 px of the part in all 8 views, real and eval
  renders alike (practice 1, 3, 8, eval 1), so matching across views pixel by pixel is sound.
- In the six straight views the silhouette's own bounding rectangle is the box face, so the scale is
  known exactly.
- In the isometric views the scale is not fixed by the box. The frame fits either the projected
  bounding box or the part itself; `iso_silhouette` tries both and accepts one only if 99.8 % of the
  silhouette falls inside the projection of the solid carved so far.

**What the silhouettes miss is open to a view, not enclosed.** After steps 1 + 2, 97 % of the excess
material (24-part eval sample, 2026-11-11) has a free line of sight to the outside along an axis:
pockets, recesses and steps that sit behind a wall in every silhouette but are open to one view. Truly
enclosed cavities are 3 %. The information is in the views as shading and face-boundary lines; what is
missing is depth.

**Round sections come out square.** A cylinder or turned profile whose axis lies across two views is
cut by both as a strip, so its cross-section stays a square: up to 4/pi = 1.27 times the true
volume. Practice outputs of steps 1 + 2 (2026-11-11): part 2 (poppet valve) is 1.23 times the true
volume, part 4 (crankshaft) 1.24.

**`context.png`** shows the part in orange inside a translucent grey assembly, from an unstated
angle. 6 of the 9 practice parts have one (not 7 and 8).

**The practice parts**, with tiers by our own rule (up to 30 faces simple, up to 200 medium, above
that complex), which gives exactly 3 of each:

| part | what | box mm | faces | holes listed | note |
| --- | --- | --- | --- | --- | --- |
| 1 | retainer bracket | 19 x 19 x 51 | 17 | 2 | blind cylindrical recess along X |
| 2 | poppet valve | 53 x 86 x 53 | 12 | 0 | cones and tori: a turned part |
| 3 | flanged bushing | 22 x 132 x 132 | 9 | 1 | sleeve with a through-bore |
| 4 | three-throw crankshaft | 212 x 197 x 565 | 178 | 0 | many fillets, no holes |
| 5 | cylinder head cover | 508 x 86 x 165 | 172 | 33 | hollow box, 15 through-passages |
| 6 | finned air-filter cone | 140 x 140 x 137 | 122 | 1 | 72 spline faces, V grooves |
| 7 | housing (no context image) | 162 x 95 x 153 | 628 | 20 | deep open cavity, 222 spline faces |
| 8 | hydraulic pump body | 80 x 128 x 40 | 306 | 19 | figure-eight through window |
| 9 | V6 engine block | 508 x 256 x 512 | 371 | 23 | two inclined cylinder banks |

## The local eval set differs from the real inputs

`datasets/eval` was built by our own tool (`tools/dataset/part_worker.py`), which copies the
organisers' format:

- **Its briefs are generated from `facts.json` by a template** ("Mechanical part ... 98 faces"). They
  hold nothing that `facts.json` does not. A step that reads the brief cannot be measured on the
  eval set; it can only be judged on the 9 practice parts, which have no ground truth.
- Its views are rendered by us with pyvista in the same style and framing. The silhouettes behave
  the same; small differences in shading are possible.
- It has no `context.png`. Each case has a `source.json` the real inputs do not have.
- Sources: 113 ABC, 85 Fusion 360 assembly, 68 Fusion 360 reconstruction. Tiers by face count.
- Rebuilding it needs `cadquery` and `pyvista` in `datasets/.venv` and the 24 GB of raw downloads.
  Neither is in `pyproject.toml`.

## How the code behaves

- **Straight cuts**: the two opposite views are mirrored onto each other and joined; a pixel is cut
  only if both views show background there. A pixel is background when its darkest channel is above
  215.
- **Outlines** (`outline_fit.py`): the traced pixel outline is split at the corners of a 1.2 px
  polygon; runs that fit a circle within 1 px and sweep at least 12 degrees become arcs, a whole
  outline that fits a circle becomes a full circle, the rest are lines. Lines drifting under 1 px
  (or 0.4 %) are made exactly horizontal or vertical. `HULL_OUTLINE=pixels|lines|arcs` switches the
  mode (default `arcs`); `pixels` is the old 0.6 px polygon.
- **The renders are hardly anti-aliased** (3 grey levels between part and background), so there is
  no sub-pixel edge information to recover; a traced slanted edge is a true staircase.
- **OpenCascade booleans with curved faces can return a wrong solid without saying so**, and
  differently per platform (found 2026-11-11 on practice part 6, a round part seen end-on): the
  result is valid, just not fully cut, or empty. On the ARM laptop a slightly enlarged circle gave
  2 to 5 % too much volume; in the amd64 image the same cut came back empty. A fuzzy tolerance does
  not help (1e-6 gives a wrong volume, 1e-4 an empty result). Flat faces are reliable. Hence the
  projection check after every cut. What made part 6 cut cleanly on both platforms was fitting
  fillets exactly tangent to the lines beside them and lines exactly onto the box faces.
- **A fitted loop can cross itself** after its corners are moved; `trace_outlines` checks every loop
  as a face and keeps the traced polygon for the ones that fail (eval part 99: 1 of 32 loops).
- **The solid is built with its bounding-box min corner at the origin**, the same frame as the
  positions in `facts.json` and the brief.
- **The verifier** (`verify.py`): `observe(case_dir)` gives mask, lines and mm per pixel of every
  view; `draw(mesh, view, dims, mm_per_px)` gives depth, face and normal per pixel of a candidate on
  the same pixel grid; `check(solid, case_dir, observed).score` is lines x silhouettes x volume
  ratio. A check takes 1 to 4 s. Lines are found as what a 5 px closing fills in (at least 28 grey
  levels darker than the surroundings).
- **The scale of an isometric view** follows from the top view: the part's extent along (1, 1, 0) is
  known there in mm and is the width of the part in the isometric view in pixels. No guess about what
  the renderer framed is needed (`verify._iso_scale`).
- **A silhouette overlap below 1 is normal for thin parts**: the ground truth itself gets 0.83 against
  its own view when the part is 12 px thick, from a one-pixel difference.
- **The tone of faces looking at the camera** is the most common part colour of a straight view. In
  the eval renders it is the same in every view (BGR 85, 141, 178); in the practice renders it varies
  by view (practice part 9: 104, 159, 202 in four views, 97, 146, 184 in two), so it is taken per view.
- **A hole of ours can show past the edge of a face**: when a listed hole is not where the view draws
  it, its floor appears as a sliver of lower level inside the neighbouring face (eval part 123).
- **A third of the lines inside the views are not reproduced by our solid, and most of them mark
  missing geometry, not only a missing split** (scratch probe, 24 eval parts, 8 views, solid after
  holes, rounds, depth and shading, 2026-11-11 night). Of 762 000 line pixels inside the part,
  244 000 (32 %, 2320 pieces) lie more than 4 px from every face boundary of our solid, so inside
  one face of ours. What the ground truth has there: a jump in depth for 52 % (93 % of those with
  our surface in front of the true one), a boundary between faces that meet smoothly or lie in one
  plane for 42 % (two thirds with excess under it, mostly eval 3; one third, 15 % of all, with our
  surface already right: a pure split, as the cylinder seams of eval 27), a crease for 2 %, nothing
  for 4 % (eval 1, 73, 181, 37: lines of our renderer with no edge in the truth). Face counts miss
  both ways: ours above `facts.json` on 13 of the 24, below on 9, equal on 2.
- **An edge is drawn onto a face with `BRepFeat_SplitShape`**, as the section of the face with the
  line swept along the view direction (2026-11-12). Pieces that meet must go in as one wire, or the
  edges do not share their corners and nothing splits; a figure that neither closes nor reaches
  the border of the face at both ends is dropped silently. The sweep also meets a cylinder on its
  far side: only section edges near the visible line are kept. `ShapeUpgrade_UnifySameDomain`
  (`vh.tidy`) undoes every such split, and a single edge along a full cylinder adds no face.
- **In an isometric view the same stretch can be 9 times shorter than in a straight view** (eval
  123 left: 0.010 against 0.090 mm per pixel), so anything read beside a line there must be taken
  further from it in the straight view, by that ratio.
- **A triangulation stays on the shape it was made for.** `verify.tessellate(solid, ..., angular)`
  with a finer angle than the default leaves that mesh on the solid, and every later
  `verify.draw` of it (level faces, shading) then sees other triangles and decides differently
  (practice 7: 2.076 to 2.090 with no cut in between). A step that needs a finer mesh takes it from
  a `BRepBuilderAPI_Copy(solid, True, False)`.
- **A solid can be valid in memory and invalid in the file.** Practice 4 after the rounds step
  passes `BRepCheck_Analyzer` before and after `vh.tidy` and fails it once the `.stp` is read back.
- **Most thin faces of the hull are not two views disagreeing about one plane** (`datums.py`,
  2026-11-12). Putting the fixed coordinates of the three silhouettes on shared values removes about
  6 % of the faces thinner than 2 px on the 24 eval parts. What is left: corner patches where a
  curved outline of one view meets a curved or slanted outline of another (eval 2, 145: inherent to
  cutting prisms, the part has a blend there), short nearly-straight pieces inside one outline, and
  the polyline outlines of eval 51 and 99, which hold 95 % of all thin faces of the sample.
- **Counting small faces by area counts real holes** (eval 147: 50 holes of r 4.5 mm are each under
  0.1 % of the area). Use the width, 2 x area / perimeter.
- **Moving an outline by a pixel can cost a thin-walled part its walls** (eval 51 with datums: the
  share of the true part inside our solid falls from about 78 % to 50 %).
- **The silhouette mask is already wider than the part**, so tracing through the centres of the
  border pixels is closer to the truth than tracing along their outer edges (2026-11-12: edges made
  14 of 22 ordinary eval parts worse, none better). The dark line drawn around the part counts as part.
- **The fitted outline can cut a thin wall through** (eval 51 top/bottom, 316 loops, walls of 1 to 2
  px: the cut returns 0.5 % of the true volume after 90 s, then the traced pixels are used; that is
  where its 1384 faces come from). Almost every loop is fitted as lines and arcs; the polylines in
  the output come from this fallback, not from loops that could not be fitted.
- **A silhouette cut can land below the true volume without being wrong** (eval 75, a plate with
  many holes: top/bottom leaves 0.956 of the true volume with fitted and with traced outlines alike,
  and matches its view). The 97 % guard then dropped the whole direction: IoU 0.675 instead of
  0.925. What looked like a fragile boolean (it "went through" with shared datums or edge tracing)
  was the volume landing just above 97 %.
- **The order of the silhouette cuts decides their time, not their result** (eval 99, 2026-11-12,
  each cut in a child process, laptop moderately loaded). Isometric cut last: 178 s for that cut.
  Third: 156 s. Second: 20 s. First, on the box: 0.4 s, and the four cuts together 48 s (the three
  straight ones alone 23 s). Same solid every time (2.318 of the true volume). The cut was "by far
  the slowest and the only one that hung" because it ran last, against a solid of thousands of faces.
- **Eval 99 is a cage of spline tubes** (320 B-spline faces, volume 2 % of the box). Its 3130 faces
  are the three lattice silhouettes crossing where the part has nothing: ghost material, 3.78 times
  the true volume. Its outlines are fitted and not reducible (about 8 segments per window).
- **The isometric cut, made first** (hull only, no holes, 2026-11-12; three cuts against four):
  eval 99 IoU 0.250 to 0.406, 38 0.576 to 0.628, 171 0.568 to 0.580, 51 0.125 to 0.128, 236 and 110
  unchanged; practice volume / true: 6 3.84 to 3.66, 7 2.65 to 2.54, 9 2.70 to 2.68, 5 unchanged.
  No part lower. It adds faces: 51 485 to 958, 171 90 to 176, practice 9 32 to 81, 7 121 to 187.
  Its scale still has to come from the finished hull (`iso_silhouette`), so "first" means carving
  twice, or taking the scale from the top view as `verify._iso_scale` does.
- **Thin-walled silhouettes are rare and easy to tell**: the share of part pixels that a 3 x 3
  opening removes is 14.5 % in the top view of eval 51 and at most 0.07 % in every other silhouette
  of the 24 eval parts and the 9 practice parts.
- **A render draws the seam of a full cylinder, cone or torus, and that line is not a boundary
  between two faces** (2026-11-12, eval 181: three cylinders, 7 faces, one line along each). Our
  face of the same surface is closed along another line, so the view's line runs through the
  inside of our face; drawn onto it, it makes two faces of one. Where the part has two half faces
  (practice 3, the bore) there are two such lines around the axis, seen in opposite views. In
  `facts.json` a full cylinder counts as one entry either way. `split.seams` leaves out the one
  line, or of several the one nearest to where our own face is closed.
- **A new later step** is a module with `apply(agent, solid, case_dir, views)` added to `LATER_STEPS`
  in `hull_agent.py` and its label to `STEPS` in `tools/eval_hull.py`.
- **`Cutter.run(operation, shape, argument, timeout)`** runs any boolean in a spawned child process.
  The operation must be a module-level function and the argument picklable. Use it for every new
  geometric step.
- **Export**: coplanar faces are merged, then the largest solid is written in millimetres.
- **A failing case does not stop the run.** `scripts/main.py` catches any exception of a case and
  writes the plain bounding box as its output instead. Inside the agent, a view direction or the
  hole step that raises is skipped and the solid carved so far is kept. Only a case whose bounding
  box cannot be read (no `facts.json` and no Envelope line) ends without a file. A hard crash of the
  main process (a segfault inside OpenCascade outside a child cut) is still not survived.
- **Tracing** (`trace.py`) never raises. It writes to `runs/` in the repo, to `/tmp` otherwise, and
  is off with `TRACE=0`. `role="stage"` on an attached `.stp` puts it in the dashboard's step strip.
- **The dashboard measures the overlap itself** (`ui/static/step-worker.js`): 40 000 fixed sample
  points, both solids laid min corner on min corner, inside-test by ray along x through the
  tessellation. On eval part 21 after the top/bottom cut it gave IoU 0.382, equal to the volume
  score, and an excess equal to the difference of the volumes to 0.2 %.
- **The dashboard's 3D diff is drawn, not computed** (`ui/static/viewer.js`): no boolean. The depth
  of each solid's nearest surface goes into a texture; then our solid is painted red wherever its
  surface lies in front of the truth's (material under that face the part does not have), and the
  truth shows in violet wherever it lies in front of ours (missing). It is what the camera sees:
  turn the part for the other sides. A first version drew our solid as a translucent red shell over
  the truth; the user found it unreadable. In three.js two materials with the same `onBeforeCompile`
  source share one program unless `customProgramCacheKey` differs. The numbers in the Diff panel
  come from point sampling instead.
- **The dashboard's section needs no cap geometry** (`ui/static/viewer.js`): where a closed solid is
  clipped, the pixels that show the inside of its skin are exactly its cut face. They are painted
  flat and written at the plane's depth (`gl_FragDepth`), so the depth comparison of the 3D diff
  treats the cut face like any other face. This only holds if the triangles are wound outwards:
  occt-import-js returns some solids (ground truths among them) wound inwards, so the worker turns a
  mesh round when its signed volume is negative. Before that fix every face of such a solid was
  drawn as cut face.
- **The dashboard** can start runs itself; those write to `runs/<run>/outputs`, not to
  `artifacts/outputs`. Only `scripts/main.py` without arguments overwrites `artifacts/outputs`.

## Submission mechanics

- CI on a pull request runs pytest and builds the image. On a push to `main` it also submits.
- pytest fails if any practice part lacks a `test_case_<n>.stp` in `artifacts/outputs`, or if that
  folder holds a file with another name.
- After code freeze the organisers run the image on the practice inputs and compare with the
  committed outputs; a mismatch is reviewed by hand. Then they run it on the hidden parts.
- At code freeze they need `<team>.env` with every variable the agent reads.
