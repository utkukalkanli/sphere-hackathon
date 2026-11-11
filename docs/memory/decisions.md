# Decisions

Newest first. Each entry says what was decided, when, and why. If a decision is reversed, replace
the entry and say what changed.

## Pipeline

- **A model will be used** (2026-11-11, user). A teammate has rented a GPU on Brev and is setting
  up a VLM and an LLM there. Until it is reachable, work goes into the deterministic steps and into
  what the model step will need from them.
- **Steps after the holes keep a cut only if the verifier does not rate the result lower**
  (2026-11-11). They can remove real material, and the volume guard only catches large mistakes.
  The verifier needs no ground truth, so the same test works on the hidden parts and for a model's
  proposals later.
- **Steps stay simple and general; no tuning to single parts** (2026-11-11, user: "do not overfit, a
  step that works correctly is enough, we improve step by step").
- **One time budget for the whole run** (2026-11-11). 17 of the 20 minutes, shared among the cases
  still to do; a case past its share skips its later steps, and the last resort is the bounding box.
- **The bosses of `facts.json` are used to make square sections round** (2026-11-11; answers "do the
  bosses help?"). Only where both side views confirm a square centred on the axis.
- **Start with a deterministic step, not a model** (2026-11-11, user). The first step builds the
  bounding box from `facts.json` and cuts away what the views show as white. It is fast, cannot
  hallucinate, and gives later steps a solid that already contains the part.
- **The isometric cut is off** (2026-11-11, user; replaces "keep it on"). It gained 0.6 to 0.8
  points, was by far the slowest cut and the only one that hung, and it shaves slanted faces off
  features it only half sees, which later steps would have to clean up. The code stays
  (`VisualHullAgent(use_iso=True)`, `tools/eval_hull.py --iso`). The isometric views are meant for
  checking candidate solids instead: `iso_silhouette` has their scale worked out.
- **Use the `facts.json` hole list as step 2** (2026-11-11, user agreed). Silhouettes only find
  holes that go all the way through along a view direction; the list also has blind and hidden
  holes, with diameter, axis, length and centre. Still no model needed.
- **When a hole group lists several lengths, cut the shortest** (2026-11-11). `lengths` holds the
  distinct lengths of the group, not one per centre, so which hole has which is unknown. Cutting too
  little costs less than cutting real material.
- **Every cut is checked against the view it was cut from** (2026-11-11). Booleans with curved
  faces can be silently wrong, so a result is only kept if its projection matches the silhouette.
- **Never cut below the true volume** (2026-11-11). `facts.json` gives the part's volume; a cut that
  leaves less than 97 % of it is undone. Every step so far only removes material, so the solid
  should always contain the part.
- **A case that fails gets the bounding box as its output, and the run goes on** (2026-11-11,
  user). A missing file scores 0 and used to take every later part down with it; a box of the right
  size still overlaps the part.
- **Every cut runs in a child process with a timeout** (2026-11-11). OpenCascade booleans can hang
  for minutes and cannot be interrupted in-process; the run has 20 minutes for all parts.
- **Outlines are rebuilt from lines and circular arcs before cutting** (2026-11-11, user; was
  postponed). Traced pixel outlines gave staircase faces, which the user saw as the main visible
  defect. Fitted circles snap to the radii and hole centres of `facts.json` when they still fit the
  pixels. If a cut with the fitted outline fails or goes below the true volume, that direction is
  cut again with the traced pixels.

## Evaluation

- **Committed practice outputs come from the `linux/amd64` image, not from a native laptop run**
  (2026-11-11). The organisers re-run the image and compare with the committed files, and booleans
  can differ between platforms (practice 6 did, before the tangent fit). Regenerate with `docker run` as in README "Code-freeze
  handover" and copy the files to `artifacts/outputs` before a submission.

- **Local score is the volume ratio `min/max`** (2026-11-11). It needs no ground-truth geometry at
  run time and matched the sampled overlap on 40 parts. It stops being valid once a step can remove
  real material or add material; then use `--iou`.
- **Routine evals use a fixed 24-part sample with caching** (2026-11-11, user). The user does not
  want minutes of laptop compute on every change. The full 266 parts only on request.
- **Eval tiers are by face count**: up to 30 simple, up to 200 medium, above that complex
  (2026-11-11). The organisers do not say how they tier; this rule puts the 9 practice parts into
  3, 3 and 3, which matches what they announce for the hidden set.
- **A 266-part local eval set in the organisers' format** (2026-11-11). The practice set has 9 parts
  and no ground truth; the hidden set is different parts, so we need many parts to avoid overfitting.

## Dashboard

- **Three columns: inputs, our solid with a strip of per-step solids, ground truth**
  (2026-11-11, user). Dark theme, minimal, little text. The user rejected the earlier layouts as too
  complicated and too wordy.
- **New pipeline steps need no UI change** (2026-11-11). A `with trace.step(...)` in the agent is
  enough; the solid after each step is saved as a stage `.stp` and shown in the strip.
- **Tracing is off in the Docker image** (`ENV TRACE=0`) (2026-11-11). It is a development aid and
  the container may only write to the output directory and `/tmp`.

## Repo

- **Feature branches, merge to `main` only to submit** (2026-11-11). CI submits on every push to
  `main`.
- **`datasets/eval` is committed; raw downloads are not** (2026-11-11). Teammates get the eval set
  from git; `datasets/raw` and `datasets/scan` are tens of GB.
