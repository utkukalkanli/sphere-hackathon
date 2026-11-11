# Open questions

Remove a question when it is answered, and record the answer in `decisions.md` or `state.md`.

## Blocking a first submission

- **Does the Docker image build and run?** It has never been built. Check it as the organisers run
  it (README, "Code-freeze handover"): `linux/amd64`, non-root user, read-only inputs. A broken
  image scores 0.
- **How long does the full run take alone on one machine?** The 20 minute limit covers all 9 parts.
  Timings so far were taken with other jobs on the same cores. Worst case by the timeouts is about
  9 x (3 x 30 + 40 + 40) s, which is over the limit, so either measure it or add a budget for the
  whole run.
- **What should happen when a case fails?** Today an exception in one case ends the run and every
  later part scores 0. The obvious fix is to catch it per case and write the bounding box, or the last
  good solid, instead.
- **Will our outputs reproduce on the organisers' machine?** They compare a re-run on the practice
  inputs with the committed files. Cuts that sit near a timeout can go either way on a different
  machine.
- **When do we make the first submission?** Nothing is on the leaderboard yet. Step 1 alone would
  already score.

## Scoring

- **What exactly does the organisers' similarity score measure?** The eval pipeline is hidden. We
  assume something close to volume overlap. If it is surface-based (for example Chamfer distance) or
  counts faces, polygonal outlines could cost more than we think.
- **Do large polygonal `.stp` files (1 to 7 MB) cause problems on their side?**

## Pipeline

- **How much does step 2 (holes) gain on the eval set?** Not measured yet. Run
  `tools/eval_hull.py` and put the table in `state.md`.
- **How much of the remaining loss is cylindrical holes, and how much is pockets and other
  cavities?** This decides whether step 3 is worth building before anything else.
- **How do we find inner cavities and pockets?** Neither silhouettes nor the hole list give them.
  Candidates: the dimensions in `description.md`, the other `facts.json` fields (fillets, partial
  cylinders, cones, face counts), the shading inside the views, or a vision model.
- **How do we measure a step that reads the brief?** The practice briefs carry feature positions and
  depths that `facts.json` lacks, but the eval briefs are templates with nothing extra. Options: write
  richer briefs for part of the eval set with a model, or judge on the 9 practice parts by volume and
  surface area only.
- **Can the true volume and surface area steer the search?** Both are given for the hidden parts.
  A step could propose several candidate cuts and keep the one that lands closest to them.
- **Do the bosses in `facts.json` help?** Step 2 only uses them to keep a boss inside a bore. A full
  convex cylinder with known diameter, axis and length also says where material around it must be
  absent.
- **Do we use a model at all, and which one?** If yes: what runs on the one RTX PRO 6000 (96 GB
  VRAM), served from the Brev instance, within the 20 minutes. `OPENAI_BASE_URL`, `OPENAI_API_KEY`
  and `OPENAI_MODEL` are not set anywhere yet, and the endpoint must be public and stay up until
  judging ends.
- **`context.png` is not used.** Is there anything in it worth using? It is absent for some parts.
- **The isometric cut is skipped when its scale cannot be confirmed** (29 of 259 eval parts). Worth
  fixing, or leave it?

## Housekeeping

- README.md's dashboard section is out of date.
- `tools/dataset` needs `cadquery` and `pyvista`, which are only in the local `datasets/.venv`.
- `.idea/` is untracked: add it to `.gitignore`?
- Should the memory files and `CLAUDE.md` stay out of the Docker image (`.dockerignore`)?
