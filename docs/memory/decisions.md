# Decisions

Newest first. Each entry says what was decided, when, and why. If a decision is reversed, replace
the entry and say what changed.

## Pipeline

- **The isometric views must be used, but never as a cut on their own: only together with
  supporting data** (2026-11-12, user: "I do not want the isometric cut used alone; with supporting
  data it works; if they give us isometric images we must use them". The user had decided this
  before and the entry was lost). How it is built now, which is this session's reading of
  "supporting data" and not yet confirmed in detail: the isometric silhouette is cut only when the
  three straight cuts leave more than 1.5 times the true volume of `facts.json`; its scale is
  confirmed on the hull of the straight cuts; it is cut first, on the box, with the straight cuts
  after it; and the result is kept only if every cut went through, less is left, and the solid
  still fills all three straight silhouettes (`hull_agent._with_iso`, `HULL_ISO_EXCESS`). This
  narrows the two entries below ("used for depth and for checking, not for carving", "the
  isometric cut is off"): the stand-alone fourth cut stays off.
- **A silhouette cut that matches its view is kept down to 90 % of the true volume, as a last
  resort** (2026-11-12, user confirmed; `HULL_TIGHT_SHARE=0.97` undoes it). Loosens "never cut below the true volume" for step 1 only: there the outline is a pixel tight
  on a thin part, and dropping the direction cost eval 75 25 points. Later steps keep the 97 % guard.
- **A silhouette with more than 1 % of its pixels in walls at most 2 px thick is traced along the
  pixel edges, all others through the pixel centres** (2026-11-12). Edges everywhere made 14 of 22
  ordinary parts worse; centres leave a thin wall no thickness (eval 51).
- **Shared datums are off by default** (2026-11-12, user). Measured on the 24 eval parts they took
  away about 6 % of the thin faces, the one IoU gain (eval 75) vanished at another tolerance, and a
  thin-walled part (eval 51) lost walls. The code stays (`HULL_DATUMS=1`). Extra faces are attacked
  at their larger source instead: outlines that stay polylines.
- **Faces are split last, whatever the face count** (2026-11-12, user asked for the step and for
  the order: line finder, then split, then the lines that mark missing geometry). Last, because any
  later cut or `tidy` would merge the faces again. A first version stopped at `faces` of
  `facts.json`; the user pointed at practice part 1, which has too many faces elsewhere and still
  lacks the line across its bottom, so the count is no longer looked at.
- **The file is checked, not only the solid** (2026-11-12). A step that makes curved faces asks
  `export.reads_back_valid`; the final export asks it too and falls back to the solid before the
  later steps. Found on practice part 4, which was valid in memory and invalid in the `.stp`.
- **Depths the solid itself implies are taken without asking the isometric view** (2026-11-12,
  after the user asked for the grooves of practice 6). The solid contains the part, so a level face
  is cut to the solid's lowest point over it and a flat face to the steepest plane through a known
  edge. What decides that an area is such a face is a line, not a tone: tones named a fin's flank a
  level face. These cuts live in the level-face step because they are the same argument.
- **Round on a disc's evidence only if the views' lines do not fit worse** (2026-11-12). Dropping
  the lathe-faces condition outright would round a square body under a round flange; the line term
  refused 3 of 10 such proposals on the eval set and none of the 6 it let through got worse.
- **`normals.py` is not wired into the shading step** (2026-11-12; the user asked for it, the
  measurement said no). As an extra "leave this face alone" test it cost eval 38 three correct cuts
  (IoU 0.639 to 0.610) and gained nothing on the other 23 parts. It stays a measurement
  (`tools/check_regions.py`) until a use is found that needs direction and not depth.
- **Tones are used to say what kind of face an area is and to check a proposed face, not to
  measure a surface** (2026-11-12, user agreed to the approach; measurements in `knowledge.md`).
  One tone is one equation for a normal with two unknowns and the table is good to a few degrees at
  best, so a free surface integrated from tones would be off by about as much as the recesses are
  deep. Radii and angles come from `facts.json`, positions from the solid and the lines.
- **The curved-recess step runs right after the rounds** (2026-11-12). It continues the profile
  the rounds step revolved, and it must see the flat ring before the level-face and shading steps
  take it for a flat floor. Confirmed by the user the same day.
- **Two tone tables ship with the agent, the organisers' renderer and our eval renderer, and the
  agent picks one per case** (2026-11-12). Without the eval table the step could not be measured
  locally. The organisers' table is the default and is only left when the other fits twice as well.
- **No model or agent in any step for now** (2026-11-11 evening, user; narrows "a model will be
  used"). Every step, including the depth-from-shading step, stays deterministic. A teammate is
  still setting up a VLM and an LLM on a rented Brev GPU; whether a later step calls them is open.
- **Depth from shading is one step after the hull, not one per view and not inside step 1**
  (2026-11-11). It needs the carved solid twice: to measure which tone each view gives each face
  direction, and to know which points the isometric camera certainly sees. Inside the step the six
  straight views are gone through one after the other, each matched against its isometric view,
  and again after a cut, because a cut can uncover the face behind it.
- **The isometric views are used for depth and for checking, not for carving** (2026-11-11). Step 5
  finds depths in them; the verifier compares candidates with them; the isometric silhouette cut
  stays off.
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

- **One big 3D view, a step slider under it, small input and output windows left and right**
  (2026-11-11 night, user; replaces the three columns). Each step must show what it extracted: the
  removed material is drawn over the solid in 3D, and the step's attachments are listed under the
  truth window on the right. The left panel holds the input and nothing else; since 2026-11-12 it is a small window on the
  pictures in the bottom left corner of the big view, not a column, with the brief and the facts folded away (user: only used to switch between pictures). The
  right window shows the ground truth, not our output (user: the output is already the big view).
  The side panels are resizable. Black background (user), minimal, little text.
- **New pipeline steps need no UI change** (2026-11-11). A `with trace.step(...)` in the agent is
  enough; the solid after each step is saved as a stage `.stp` and becomes a stop on the slider.
  Whatever the step attaches as an image or a `.json` list shows up as "extracted". The one-line
  description under each stop comes from a list in `ui/static/app.js` (`ABOUT`); a new step adds a
  line there or passes `description="..."` to `trace.step`. For the step to show on the slider
  before it has run, its trace name also goes into `STEP_NAMES` in `hull_agent.py` (2026-11-12).
- **Tracing is off in the Docker image** (`ENV TRACE=0`) (2026-11-11). It is a development aid and
  the container may only write to the output directory and `/tmp`.

## Repo

- **Feature branches, merge to `main` only to submit** (2026-11-11). CI submits on every push to
  `main`.
- **`datasets/eval` is committed; raw downloads are not** (2026-11-11). Teammates get the eval set
  from git; `datasets/raw` and `datasets/scan` are tens of GB.
