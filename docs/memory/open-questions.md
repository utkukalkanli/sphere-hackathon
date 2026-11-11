# Open questions

Remove a question when it is answered, and record the answer in `decisions.md` or `state.md`.

## Blocking a first submission

- **Will the timeouts ever fire on the organisers' machine?** The practice run takes 19 s on the
  laptop and no cut came near a timeout, and the amd64 container gave the same solids. But the worst
  case by the timeouts is 9 x (3 x 30 + 40) s, about the whole 20 minutes, and a cut that times out
  there but not here changes the output. A budget for the whole run would close this.
- **When do we make the first submission?** Nothing is on the leaderboard yet. The image and the
  outputs of steps 1 + 2 are ready on `feat/visual-hull`; it takes a merge to `main`, which the user decides.

## Scoring

- **What exactly does the organisers' similarity score measure?** The eval pipeline is hidden. We
  assume something close to volume overlap. If it is surface-based (for example Chamfer distance) or
  counts faces, polygonal outlines could cost more than we think.

## Pipeline

- **How do we get depth?** 97 % of the material we still have too much of is visible from an axis
  direction (open pockets, recesses, steps behind a wall), so a cut that knows how deep each visible
  face lies would take the sampled IoU from 0.83 to about 0.97. Silhouettes have no depth. Candidates:
  match the face-boundary lines between the orthographic views, as in reconstruction from
  engineering drawings; the dimensions in `description.md`; a vision model proposing pockets, each
  checked against the true volume and the isometric silhouettes.
- **How do we measure a step that reads the brief?** The practice briefs carry feature positions and
  depths that `facts.json` lacks, but the eval briefs are templates with nothing extra. Options: write
  richer briefs for part of the eval set with a model, or judge on the 9 practice parts by volume and
  surface area only.
- **Can the true volume and surface area steer the search?** Both are given for the hidden parts.
  A step could propose several candidate cuts and keep the one that lands closest to them.
- **Do the bosses in `facts.json` help?** Step 2 only uses them to keep a boss inside a bore. A full
  convex cylinder with known diameter, axis and length also says where material around it must be
  absent.
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
