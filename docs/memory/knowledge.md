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
- In the six straight views the silhouette's own bounding rectangle is the box face, so the scale is
  known exactly.
- In the isometric views the scale is not fixed by the box. The frame fits either the projected
  bounding box or the part itself; `iso_silhouette` tries both and accepts one only if 99.8 % of the
  silhouette falls inside the projection of the solid carved so far.

**A through hole does not always show background in the render.** Practice 8's eight 7 mm flange
holes are through (facts.json, genus 11, and cutting them brings the volume to 1.008 x the true
one), yet the top view shows tan shading with a cone at their centres, not white (measured
2026-11-11). So "the render shows the part here" cannot veto a hole cut: a check that did
dropped practice 8 from 0.992 to 0.904. Other through holes (practice 1) do show white.

**What the silhouettes miss is open to a view, not enclosed.** After steps 1 + 2, 97 % of the excess
material (24-part eval sample, 2026-11-11) has a free line of sight to the outside along an axis:
pockets, recesses and steps that sit behind a wall in every silhouette but are open to one view. Truly
enclosed cavities are 3 %. The information is in the views as shading and face-boundary lines; what is
missing is depth.

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
- **Booleans can differ between platforms.** A circle that touches the box faces (a round part seen
  end-on, practice part 6) cuts fine on the ARM laptop and fails in the amd64 image, where the
  traced-pixel fallback takes over. A fuzzy tolerance on the cut might remove the difference; not tried.
- **The solid is built with its bounding-box min corner at the origin**, the same frame as the
  positions in `facts.json` and the brief.
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
- **The dashboard used to kill terminal runs on Windows** (fixed 2026-11-11). `pid_alive` called
  `os.kill(pid, 0)`, which on Windows is `TerminateProcess` with exit code 0: every traced run the
  dashboard had not started died silently, with exit code 0, while the dashboard was open.
- **`views.py`** has the cameras of all eight renders (`OrthoCamera`, `IsoCamera.fit`) and
  `ViewSet.missing`; on eval ground truth the projected silhouettes reach IoU 0.97-1.0 in every
  view. `tools/iso_check.py` draws the coordinate system on the iso renders and the differences.
- **The dashboard** can start runs itself; those write to `runs/<run>/outputs`, not to
  `artifacts/outputs`. Only `scripts/main.py` without arguments overwrites `artifacts/outputs`.

## Submission mechanics

- CI on a pull request runs pytest and builds the image. On a push to `main` it also submits.
- pytest fails if any practice part lacks a `test_case_<n>.stp` in `artifacts/outputs`, or if that
  folder holds a file with another name.
- After code freeze the organisers run the image on the practice inputs and compare with the
  committed outputs; a mismatch is reviewed by hand. Then they run it on the hidden parts.
- At code freeze they need `<team>.env` with every variable the agent reads.
