# Sphere Forge #1: prompt to CAD (team-d)

Hackathon entry. For each test case (a design brief, `facts.json` and 8 rendered views of one
mechanical part) the agent writes one solid as `test_case_<n>.stp`. The score is geometric similarity
to a hidden ground truth. The organisers' rules are in [README.md](README.md); read it before
touching the submission format, the Dockerfile or CI.

## Project memory

These four files are the shared memory of the project. They are loaded with this file at the start
of every session:

- What exists, what is in progress, the latest measurements: @docs/memory/state.md
- What was decided and why: @docs/memory/decisions.md
- What is undecided or unknown: @docs/memory/open-questions.md
- What we learned about the inputs, the eval set and the code: @docs/memory/knowledge.md

Keep them current as part of the work, without being asked:

- When a step is finished, measured, committed or abandoned, update `state.md`. Replace old text;
  it describes the present, not the history.
- When the user decides something, or a measurement settles a choice, add it to `decisions.md` with
  the date and the reason. If it answers an open question, remove that question.
- When something comes up that nobody can answer yet, add it to `open-questions.md`.
- When reading or measuring shows how the inputs, the eval set or the code really behave, put it
  in `knowledge.md`.
- Give numbers with what they were measured on (which set, how many parts, which commit or date).
- Several sessions often work in this repo at once. Read a memory file again just before editing it,
  and change only the lines that are yours to change.

## Rules that are easy to break

- **A push to `main` is a submission.** CI builds the image, pushes it to ECR and uploads
  `artifacts/outputs` to S3; a commit can be submitted only once. Work on a branch and merge to
  `main` only when the user asks.
- **Do not change the submission format**: entrypoint `python scripts/main.py --input_dir <dir>
  --output_dir <dir>`, outputs named `test_case_<n>.stp`, one solid each, in millimetres. A missing,
  broken or non-solid file scores 0.
- **The whole run has 20 minutes** for all test cases, as a non-root user that can write only to the
  output directory and `/tmp`.
- **Build for unseen parts.** The final ranking uses 9 hidden parts (3 simple, 3 medium, 3 complex).
  Never tune to the 9 practice parts.
- **`datasets/`, `ui/` and `runs/` never enter the Docker image**, and the agent must not read
  ground truth. Everything the agent needs is in `src/` and `scripts/`.
- **Do not run the full 266-part eval unless the user asks.** It takes minutes of all cores on the
  user's laptop. Use the cached 24-part sample (`tools/eval_hull.py` without `--all`).
- **Dashboard (`ui/`): keep it minimal**, with little text. The user has rejected busy layouts twice.

## Commands

```sh
uv run python scripts/main.py                          # practice set -> artifacts/outputs (overwrites)
uv run python scripts/main.py --only test_case_5       # one case
uv run python scripts/main.py --input_dir datasets/eval/inputs --output_dir <dir>
uv run python tools/eval_hull.py                       # score on 24 fixed eval parts, cached
uv run python tools/eval_hull.py --iou                 # also check the overlap against ground truth
uv run python tools/check_verify.py                    # verifier scores of ground truth, our solid and the box
uv run python ui/server.py                             # dashboard, http://localhost:8765
uv run pytest                                          # what CI runs
```

## Layout

| path | contents |
| --- | --- |
| `scripts/main.py` | entrypoint the organisers run |
| `src/agentic_cad/` | the agent: `visual_hull.py` (step 1), `holes.py` (step 2), `rounds.py` (step 3), `depth.py` (step 4), `shading.py` (step 5), `verify.py` (checks a candidate solid against the inputs), `hull_agent.py` (runs the steps), `trace.py` (run traces for the dashboard) |
| `artifacts/inputs` | the 9 practice parts; `artifacts/outputs` holds our committed answers for them |
| `datasets/eval` | 266 extra parts in the same format, with ground truth, for local scoring |
| `tools/eval_hull.py` | local score; `tools/dataset/` built the eval set |
| `ui/` | development dashboard: inputs, our solid after every step, ground truth |
| `runs/` | traces and eval cache, git-ignored |
