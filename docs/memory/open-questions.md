# Open questions

Remove a question when it is answered, and record the answer in `decisions.md` or `state.md`.

## Blocking a first submission

- **How fast is the organisers' machine?** The practice run now takes 87 s on the laptop (steps 1
  to 4), and the run budget in `scripts/main.py` guarantees a file for every case within 17 minutes.
  What stays open: a cut that times out or a step that is skipped there but not here changes the
  output, and the image has not been run with steps 3 and 4 yet.
- **When do we make the first submission?** Nothing is on the leaderboard yet. The image and the
  outputs of steps 1 + 2 are ready on `feat/visual-hull`; it takes a merge to `main`, which the user decides.

## Scoring

- **What exactly does the organisers' similarity score measure?** The eval pipeline is hidden. We
  assume something close to volume overlap. If it is surface-based (for example Chamfer distance) or
  counts faces, polygonal outlines could cost more than we think.

## Pipeline

- **How do we get the depth of blind pockets?** Step 4 only spreads a level our solid already has
  over the face it belongs to (0.847 to 0.854 on the 24-part sample). Most of the remaining excess is
  recesses our solid reaches nowhere. Step 5 (`shading.py`) takes the depth of flat floors from the
  isometric views (0.854 to 0.856). Still open: floors the isometric camera does not certainly see
  (deep or narrow pockets): a second, tone-only search now takes the large ones (0.856 to 0.861).
  Still missed: floors not in sight of either isometric camera at all (eval 38: the inside of a
  U-channel, tone share 0 at the true depth), which need another source, such as the lines of the
  two perpendicular straight views; small floors; and floors only partly in sight (eval 236 and 171
  back). Also open: slanted faces with no neighbour of known depth (practice 9), which
  need the tone turned into a slope by a shading model fitted per case, since searching direction,
  slope and depth freely gave false cuts (eval 74); curved faces; the dimensions in `description.md`.
- **How do we turn tones into geometry?** (2026-11-12) The tone gives one equation per pixel for a
  normal with two unknowns, through a table that is the same for all parts (`knowledge.md`). Idea
  agreed with the user so far: do not integrate a free surface; use the tone to say which regions
  are flat, slanted or curved and which way they fall, and fit planes, cylinders, cones and tori
  whose radii and angles are in `facts.json`. Open: where the table comes from at run time (a fixed
  one shipped with the agent, measured on the practice renders, or built per case from the faces of
  the hull, which in a blocky part cover few directions); how a region gets its depth when it
  touches no face of known depth; whether the steps should share one table of regions per view
  (class, normal, depth, source) instead of each finding its own.
  `regions.py` gives the areas and their kind, `normals.py` says which flat areas the isometric
  view confirms as the solid's own face. Still open: a slanted face the solid does not have (its
  depth is unknown, so it cannot be looked up in the isometric view; 12 % of the slanted area on
  the eval sample is still called facing), and what to do with the flat areas that are not
  confirmed: they mark where the solid is wrong, which steps 4 and 5 could use to choose where to
  search and where to leave a face alone.
- **Grooves between fins** (practice 6; user, 2026-11-12: this is what the shading work was meant
  to fix). Done without tones for flat faces that stand on a visible level face (`depth.py`, see
  `state.md`). Open: flanks seen only from the side views, which have no level face to hang on;
  the banks of practice 9, which stand on nothing visible; small areas on thin-walled parts, where
  the edge-on line is found by chance (eval 99).
- **Nothing checks a round cut against the views.** On practice 6 the "disc" cut of the cap's last
  10 mm came back with the section still square and was kept. The hull cuts have a projection check;
  `rounds.py` and `curved.py` do not.
- **Hollow parts** (practice 6: a shell, about two thirds of its excess). Only `volume_mm3` and
  `surface_area_mm2` show it. Not looked at; the user has set it aside for now.
- **Curved faces that are not rings around a listed axis** (2026-11-12). `curved.py` only handles
  recesses of revolution about a boss or bore axis along x, y or z. Fillets along straight edges,
  cylindrical pockets seen from the side and anything about a tilted axis are untouched. On the
  eval set the step found work on 5 of 37 lathe-type parts, so how much the hidden parts gain is
  unknown.
- **Is the organisers' tone table good enough?** It was measured on our own solids of the 9 practice
  parts, which are wrong in places, and fills 692 of about 1300 directions; the rest is
  interpolated. A wrong table makes `curved.py` drop right proposals (it cannot make it cut).
- **What counts as supporting data for the isometric cut?** (2026-11-12) The user wants the
  isometric views used, never alone (`decisions.md`). Built so far: true volume as the trigger, the
  straight hull for the scale, the straight silhouettes as the check. Not used yet: the lines and
  tones of the views (the verifier), which could accept or refuse the cut per region instead of as a
  whole, and could allow it on parts under the 1.5 threshold.
- **Should the isometric support run from 1.05 times the true volume instead of 1.5?** (2026-11-12,
  put off by the user.) Hull + holes: IoU 0.8541 to 0.8574, none lower (`state.md`). Before it is
  switched on: run the whole pipeline on the 24 parts and the practice set with
  `HULL_ISO_EXCESS=1.05`, and look at practice 2 and 4, where it shaves the square sections the
  rounds step looks for. The 1 % minimum gain and the piece-by-piece path are unmeasured too.
- **How do we measure a step that reads the brief?** The practice briefs carry feature positions and
  depths that `facts.json` lacks, but the eval briefs are templates with nothing extra. Options: write
  richer briefs for part of the eval set with a model, or judge on the 9 practice parts by volume and
  surface area only.
- **Can the true volume and surface area steer the search?** Both are given for the hidden parts.
  A step could propose several candidate cuts and keep the one that lands closest to them.
- **Next step after shading: use the rest of `facts.json`** (user, 2026-11-11). Read so far: the
  bounding box, the volume, the two full-cylinder lists (and, in `rounds.py`, the face types to
  recognise a turned part). Not used yet: `surface_area_mm2` (only inside the verifier's report),
  `faces`, `edges`, `through_holes_genus`, `partial_cylinders_arcs`,
  `fillets_rounds_from_cylinders`, `fillets_from_tori_minor_radii`, `cones_half_angle_deg`. The step
  should extract these and apply them (fillet and arc radii, cone angles, counts as a check on a
  candidate). Deterministic, like every step for now.
- **Which model, and what does it do?** A model will be used (see `decisions.md`); a teammate is
  setting up a VLM and an LLM on the Brev instance. Open: which models run on the one RTX PRO 6000
  (96 GB VRAM) within the 20 minutes, and which step calls them. `OPENAI_BASE_URL`, `OPENAI_API_KEY`
  and `OPENAI_MODEL` are not set anywhere yet, and the endpoint must be public and stay up until
  judging ends.
- **What should read the evidence table?** (2026-11-12, plan phase 2.) `evidence.rows` flags
  regions with lines under our surface and facing neighbours that are coplanar in our solid, and
  nothing uses the flags: cuts from coplanar pairs gained 0.004 outside the parts they were
  developed on. They are meant for ordering proposals and as a model's input (plan phase 3). Not
  measured: how right the flags are against the ground truth.
- **`context.png` is not used.** Is there anything in it worth using? It is absent for some parts.
- **How deep is a blind hole that `facts.json` lists only as a span?** (2026-11-12, practice 8,
  `knowledge.md`.) `spans.py` cuts a blind hole from each end to half of the room it has (5.05 mm
  for the d7 holes, 5.94 mm for the d4.2 ones); that is a guess. Known bounds on practice 8: deeper
  than about 0.7 diameters (the isometric view shows no bottom), and the d7 holes at x 16 and 64
  have 10.1 mm before they would reach the main bore. Unused: that lower bound, and whether the true volume can fix the depth once the rest
  of the part is right. The depth is worth up to 3.7 of the 8.1 cm3 of excess on practice 8
  (`knowledge.md`, "Where practice 8 differs from its views").
- **The face of a single mouth** (2026-11-12). `mouths.py` cuts the area around mouths down to
  their plane only where two mouths agree. One mouth can lie at the bottom of a counterbore or
  chamfer narrower than a line (practice 7), and nothing in the view tells that from a mouth flush
  with the face. `facts.json` lists the wider coaxial cylinder or the cone in such a case; not used.
  The face rule itself has no ground-truth measurement: no eval part of the sample has two buried
  mouths on one plane.
- **Features that stand only over part of the height** (2026-11-12, practice 8: bolt lobes that
  are a boss at the top and at the bottom and absent in between; `knowledge.md`). A silhouette is
  the union over the whole depth, so the hull keeps them as full prisms; the surface behind is
  curved, so level faces, shading and pockets do not apply. Idea, not built: a silhouette cut
  restricted to a region, with an occluding contour drawn inside a perpendicular view as the
  profile and the figure of the facing view as the region. Unknown how often hidden parts have
  this and whether the eval set can measure it.
- **Pockets from lines under our surface: what is left to gain?** (2026-11-12) Lines under our
  surface exist on 7 of the 24 eval parts. Taken now: closed figures and figures that end on
  another line of the view (eval 236, 171). Not pockets at all: the seams of a curved surface our
  blocky hull covers (eval 3, a tyre, more than a third of all such lines). Still open: figures
  on thin-walled parts that do not close (eval 51, 99); eval 38, whose floor no isometric camera
  sees; a figure closed straight across under a curved rim leaves a sliver uncut; rings around
  listed holes with the cone angles of `facts.json` (eval 123), not tried.
- **Does the split step help the score?** It changes the number of faces and edges only. If the
  organisers' score is volume overlap it gains nothing; it is in because the user wants the face
  count right. It also splits few faces so far: figures need joining across pieces the isometric
  camera does not see, and pieces along one circle are still fitted as several short lines.

- **Should each case run in its own process?** (2026-11-12, left out of plan phase 0.) A segfault
  in the main process now leaves every case a valid file, but the cases after it stay at the box or
  the hull. A process per case would go on with the next case and would bound the hull by the
  case's deadline, which nothing does today; tracing lives in the main process.
- **A kill in the first second, or between a copy and its rename** (2026-11-12). Before the boxes
  are written (0.7 to 1.1 s, the import of OpenCascade) there are fewer than 9 files. A file waits
  in the output directory as `.test_case_<n>.stp.part` for the moment of the rename; a SIGKILL in
  those milliseconds leaves it there (not seen in 7 kills). What the organisers do with an extra
  file is not known.
- **The image and the laptop give different solids on practice 6, 7 and 9** (2026-11-12,
  `state.md`). The committed outputs must come from the image anyway; open is whether the
  organisers' faster machine gives a third result (see the wall-clock budgets below).
- **The case's share of the run is the one clock left** (2026-11-12). The steps' own budgets are
  gone; a case in the third pass may use two equal shares of the time left. On a machine three or
  four times slower than the laptop (the emulated image) practice 7 would still be stopped. Not
  measured: the image after this change.
- **How does a judge tell a right cut with an uncut neighbour from a wrong cut?** (2026-11-12,
  after `tools/check_judge.py`, `state.md`.) The local rule refuses 15 of 27 right cuts: the walls
  a cut leaves against excess that is still there are faces the part does not have, and their
  tones and edges count against it (eval 236, 38). The score rule lets them through because the
  volume term pays for them. Phase 3 needs the stricter rule for a model's guesses; open is
  whether new walls should be excused (then a column cut in a facing area has no tone evidence
  left at all: its floor has the tone of the face above it), or cuts judged in groups.
- **The 97 % volume guard is a refusal on the part's total** (2026-11-12). The judge does not ask
  that the volume comes closer to the true one (`decisions.md`, "judged where it acts"), but it
  keeps the guard, as every step does. On a part whose volume is right for a wrong reason
  (practice 8 before the blind holes) the guard refuses right cuts.
- **How does the organisers' scorer treat an enclosed void, and is the score volume or surface?**
  A first submission would show it if the leaderboard gives a score per part: practice 6 near 0.3
  means volume overlap, 0.55 or more a surface or composite score. Carving the core of a shell
  (practice 6) waits on this.

## Housekeeping

- README.md's dashboard section is out of date.
- `tools/dataset` needs `cadquery` and `pyvista`, which are only in the local `datasets/.venv`.
- `.idea/` is untracked: add it to `.gitignore`?
- Should the memory files and `CLAUDE.md` stay out of the Docker image (`.dockerignore`)?
