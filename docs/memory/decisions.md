# Decisions

What was decided, when, and why, grouped by subject. An entry that is reversed is replaced, not
kept beside its successor. Older wording: `git show bda2104:docs/memory/decisions.md`.

## Principles

- **Steps stay simple and general; no tuning to single parts** (2026-11-11, user: "do not
  overfit, a step that works correctly is enough"). No threshold is chosen on the 9 practice
  parts.
- **Deterministic first; a model only proposes** (2026-11-11 user: start without a model, and
  "no model in any step for now"; 2026-11-12 plan phase 3). The pipeline carves a solid that
  contains the part from exact inputs. The model step is built and stays off until an endpoint
  exists and it has passed a test on parts with ground truth.
- **The solid should always contain the part.** Every step only removes material; a cut that
  leaves under 97 % of the true volume of `facts.json` is undone. One exception: a silhouette cut
  that matches its view is kept down to 90 % (2026-11-12, user confirmed; the outline is a pixel
  tight on thin parts, and dropping the direction cost eval 75 25 points).
- **A change is judged where it acts, not by the part's total** (2026-11-12, user). A volume
  ratio, verifier score or IoU can worsen through a right change that stops cancelling an error
  elsewhere (practice 8: holes cut through hid the hull's excess). Measure a step on the region
  it changes, report the total beside it, and never refuse or tune a step on the total alone.

## Step 1: silhouettes

- **Outlines are rebuilt from lines and circular arcs before cutting** (2026-11-11, user).
  Traced pixels gave staircase faces. Circles snap to radii and centres of `facts.json`. If the
  fitted cut fails, that direction is cut again with traced pixels.
- **Every cut is checked against the view it was cut from** (2026-11-11). Booleans with curved
  faces can be silently wrong.
- **Thin-walled silhouettes are traced along pixel edges, all others through pixel centres**
  (2026-11-12). Edges everywhere made 14 of 22 ordinary parts worse; centres leave a thin wall no
  thickness (eval 51).
- **The isometric views must be used, but never as a cut on their own** (2026-11-12, user; the
  stand-alone fourth cut has been off since 2026-11-11: under one point of gain, slowest cut,
  shaves features it half sees). They are used three ways: as support for the hull when the
  straight cuts leave more than 1.5 times the true volume, kept only if the straight silhouettes
  and the views' lines agree; for depths (shading, pockets, mouths); and for checking (verifier,
  judge). What exactly counts as "supporting data" is this project's reading, not confirmed in
  detail by the user.
- **Shared datums are off** (2026-11-12, user). They removed 6 % of the thin faces, their one IoU
  gain vanished at another tolerance, and eval 51 lost walls.

## Holes

- **The `facts.json` hole list is step 2** (2026-11-11, user). It has blind and hidden holes that
  no silhouette shows.
- **When a group lists several lengths, cut the shortest** (2026-11-11). Which hole has which
  length is unknown; cutting too little costs less.
- **A span the views show closed is two blind holes, each cut to half of its room** (2026-11-12).
  The depth is a guess (`open-questions.md`).
- **The face around mouths is cut only where two mouths agree on its plane** (2026-11-12). One
  mouth can sit in a recess too narrow to be drawn (practice 7).

## Later steps

- **Bosses of `facts.json` make square sections round**, only where both side views confirm
  (2026-11-11). On a disc's evidence alone only if the views' lines do not fit worse
  (2026-11-12).
- **Curved recesses run right after the rounds** (2026-11-12, user confirmed): they continue the
  revolved profile and must see the flat ring before level faces take it for a floor.
- **Tones say what kind of face an area is and check a proposed face; they do not measure a
  surface** (2026-11-12, user agreed). One tone is one equation for two unknowns. Radii and
  angles come from `facts.json`, positions from the solid and the lines.
- **Two tone tables ship, the organisers' renderer and our eval renderer, chosen per case**
  (2026-11-12). The organisers' is the default, left only when the other fits twice as well.
- **Depths the solid itself implies are taken without the isometric view** (2026-11-12): a level
  face down to the solid's lowest point over it, a flat face to the steepest plane through a
  known edge. A line decides that an area is such a face, not a tone.
- **Eval 99 is left a little worse by those cuts** (2026-11-12, user: "leave it"). A threshold
  that spared it would have been tuned to the sample.
- **Shading is one step after the hull** (2026-11-11): it needs the carved solid to measure
  tones and visibility.
- **`normals.py` is not wired into shading** (2026-11-12; the user asked, the measurement said
  no): as a guard it cost eval 38 three right cuts and gained nothing.
- **Faces are split last, whatever the face count** (2026-11-12, user). Any later cut or `tidy`
  would merge them again; a cap on the count kept practice 1 from getting its missing line.

## Accept rules

- **Later steps keep a cut only if the verifier's score does not drop** (2026-11-11; confirmed
  2026-11-12 by measurement: on the cuts the steps propose the score rule kept no wrong cut that
  could be graded, the local rule refused more than half of the right ones).
- **A model's cut must pass the local judge and make the views fit strictly better**
  (2026-11-12). A model's cuts are guesses; the score rule passes most wrong arbitrary cuts. A
  cut no view sees stays out even when it is right.
- **The judge does not ask that the volume comes closer to the true one** (2026-11-12, against
  the plan): that would refuse a right cut on the part's total. The 97 % guard stays.
- **The judge lets the outline worsen by 0.2 % of a view's part** (2026-11-12): step 1's
  tolerance, taken over so as not to pick a number on the practice parts.

## Run safety and time

- **Every case has a valid file from the first second: box first, then only better files**
  (2026-11-11 user: a failing case gets the box and the run goes on; 2026-11-12 phase 0). A
  missing or broken file scores 0.
- **The file is checked, not only the solid** (2026-11-12). Every later step's result is written
  and read back; a step that fails is dropped alone.
- **Every boolean runs in a child process with a timeout** (2026-11-11). OpenCascade can hang
  for minutes and cannot be interrupted.
- **One time budget for the run, 17 of the 20 minutes; steps are bounded by counts, not clocks**
  (2026-11-11, 2026-11-12). A clock per step makes the solid depend on the machine.

## Evaluation

- **Local score is the volume ratio `min/max`; `--iou` once a step can remove real material**
  (2026-11-11).
- **Routine evals use the fixed 24-part cached sample; the 266 only on request** (2026-11-11,
  user).
- **Tiers by face count**: up to 30 simple, up to 200 medium, above complex (2026-11-11). It
  puts the practice parts into 3, 3 and 3.
- **A 266-part local eval set in the organisers' format** (2026-11-11), committed; raw downloads
  are not.
- **Committed practice outputs come from the `linux/amd64` image** (2026-11-11). The organisers
  re-run the image and compare; booleans differ between platforms.

## Dashboard

- **Two views and a link** (2026-11-12, user): left the input and ground truth, right our solid,
  "Extracted" floating on the boundary. The left never shows our output. Under them only the
  steps and Diff. What a step does goes in a tooltip, not in text under the slider.
- **Minimal: black background, little text, no rules or nested frames** (user, several times).
  The case list stays in number order.
- **Every case shows the same stops, whether a step ran or not** (2026-11-12, user).
- **A new step needs no UI change**: `with trace.step(...)` makes a stop. For the stop to exist
  before the step has run, add its trace name to `STEP_NAMES` in `hull_agent.py`; for old traces,
  to `STOPS` in `ui/static/app.js`; its one-line description to `ABOUT` there.
- **Tracing is off in the Docker image** (`ENV TRACE=0`).

## Repo

- **Feature branches; merge to `main` only to submit, when the user says so** (2026-11-11).
