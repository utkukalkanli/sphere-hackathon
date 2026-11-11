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
- **Do we use a model at all, and which one?** If yes: what runs on the one RTX PRO 6000 (96 GB
  VRAM), served from the Brev instance, within the 20 minutes. `OPENAI_BASE_URL`, `OPENAI_API_KEY`
  and `OPENAI_MODEL` are not set anywhere yet, and the endpoint must be public and stay up until
  judging ends.
- **`context.png` is not used.** Is there anything in it worth using? It is absent for some parts.
- **The isometric cut is skipped when its scale cannot be confirmed** (29 of 259 eval parts). Worth
  fixing, or leave it?

## Housekeeping

- README.md's dashboard section is out of date.
- `.idea/` is untracked: add it to `.gitignore`?
- Should the memory files and `CLAUDE.md` stay out of the Docker image (`.dockerignore`)?
