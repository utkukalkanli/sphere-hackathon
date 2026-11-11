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
- **The dashboard** can start runs itself; those write to `runs/<run>/outputs`, not to
  `artifacts/outputs`. Only `scripts/main.py` without arguments overwrites `artifacts/outputs`.

## Submission mechanics

- CI on a pull request runs pytest and builds the image. On a push to `main` it also submits.
- pytest fails if any practice part lacks a `test_case_<n>.stp` in `artifacts/outputs`, or if that
  folder holds a file with another name.
- After code freeze the organisers run the image on the practice inputs and compare with the
  committed outputs; a mismatch is reviewed by hand. Then they run it on the hidden parts.
- At code freeze they need `<team>.env` with every variable the agent reads.
