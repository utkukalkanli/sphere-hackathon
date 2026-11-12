# Open questions

Remove a question when it is answered, and record the answer in `decisions.md` or `state.md`.

## Blocking a first submission

- **How fast is the organisers' machine?** The practice run now takes 87 s on the laptop (steps 1
  to 4), and the run budget in `scripts/main.py` guarantees a file for every case within 17 minutes.
  What stays open: a cut that times out or a step that is skipped there but not here changes the
  output, and the image has not been run with steps 3 and 4 yet.
- **Practice part 4 reads back as an invalid solid once the rounds step has run** (see Known
  weaknesses in `state.md`). Whether the organisers' scorer rejects it is unknown; a broken file
  scores 0. Open: what exactly is invalid, whether a repair on export fixes it, and whether the
  export should read every file back and fall back to the solid before the offending step.
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
- **Grooves between fins: slanted flat faces with no neighbour of known depth** (practice 6, and
  the banks of practice 9; user, 2026-11-12: this is what the shading work was meant to fix). Each
  flank is a flat area of the end view with one even tone, between a crest line that lies on the
  solid's surface and a valley line whose depth is unknown. Ideas not yet tried: the plane through
  the crest (known in 3D once the section is round) with the slope the tone gives; or the valley
  line found in a side view too and placed in 3D from the two views.
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
- **`context.png` is not used.** Is there anything in it worth using? It is absent for some parts.
- **Pockets from lines under our surface: what is left to gain?** (2026-11-12) Measured on the 24
  eval parts (`state.md`): lines under our surface exist on 7 parts only, and 23 % of their length
  closes into a figure. What does not: seams of a curved surface our blocky hull covers (eval 3, a
  tyre: more than a third of all such lines, not pockets at all); outlines that share a line with
  their neighbour, which `split.figures` leaves open where three lines meet (eval 236, the larger
  rectangles); thin-walled parts (eval 51, 99, 171). Eval 38 closes but our surface over the
  figure is not one level, and its floor is out of sight of the isometric cameras anyway. Open:
  is joining at three-way corners worth it for the hidden parts, and rings around listed holes
  with the cone angles of `facts.json` (eval 123), which are not tried.
- **Does the split step help the score?** It changes the number of faces and edges only. If the
  organisers' score is volume overlap it gains nothing; it is in because the user wants the face
  count right. It also splits few faces so far: figures need joining across pieces the isometric
  camera does not see, and pieces along one circle are still fitted as several short lines.

## Housekeeping

- README.md's dashboard section is out of date.
- `tools/dataset` needs `cadquery` and `pyvista`, which are only in the local `datasets/.venv`.
- `.idea/` is untracked: add it to `.gitignore`?
- Should the memory files and `CLAUDE.md` stay out of the Docker image (`.dockerignore`)?
