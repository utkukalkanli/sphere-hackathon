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
  recesses our solid reaches nowhere, so their depth must come from elsewhere: the isometric views
  and the shading (`shading.py`, in progress in another session), the dimensions in
  `description.md`, or a vision model proposing pockets, each checked by the verifier.
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
## Housekeeping

- README.md's dashboard section is out of date.
- `tools/dataset` needs `cadquery` and `pyvista`, which are only in the local `datasets/.venv`.
- `.idea/` is untracked: add it to `.gitignore`?
- Should the memory files and `CLAUDE.md` stay out of the Docker image (`.dockerignore`)?
