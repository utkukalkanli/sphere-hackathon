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
- **A slanted face is cut only if that lowers the solid by 2 px measured square to the new
  face** (2026-11-12, after the user saw the clipped corner on eval 1). Along the view, the pixel
  of uncertainty in the edge it hangs on is 6.6 px on a face at 81 degrees, and a face that was
  right was cut again a hair steeper.
- **Lines on dark faces stay out of the line map** (2026-11-12, measurement): they raise the
  share of true edges found from 92.1 to 94.0 % and leave the 24 parts where they were (volume
  score 0.8972 to 0.8981, sampled IoU 0.8928 to 0.8920, 14 faces more a solid). Behind
  `HULL_FAINT_LINES=1`.
- **What the pictures say by themselves is read first, as a step of every case, by code in
  `src/`** (2026-11-12, user: the sources must be a step at the start of every run, and nothing
  may be made by hand and handed to the code). `read_sources` runs after the box; a failure
  costs only the steps that use it. Pictures are written only when tracing.
- **A view is divided into faces first; its edges are the borders of that division**
  (2026-11-12, after the user found line pixels too rough to work on). Found edge pixels leave
  gaps, and one gap joins two faces; borders of a division are closed by construction. They are
  then fitted with lines and arcs to 0.6 px.
- **Where a border is a meeting and where one face hides another is decided with our solid, not
  from the picture alone** (2026-11-12, user: go on with the solid). The T-junctions alone are
  right for 81 %; where our solid is level across a border of two facing faces, the T names the
  deeper side right for 12 of 13.
- **A recess found that way gets its depth by cutting and judging**, as the model's recesses do
  (2026-11-12): the picture says where, nothing in it says how deep.
- **Without a T both sides of the border are searched and the judge chooses** (2026-11-12, user).
  Eval 38 0.9371 to 0.9926.
- **A recess is looked for only where our solid is flat and square to the view under both
  faces** (2026-11-12). The strip of a tyre's tread that faces the camera reads as a flat facing
  face in the eval renders, and the judge kept columns 20 to 36 mm deep in it (eval 3, 0.9172 to
  0.9126). Asking instead for one depth under each face cost eval 38 a right cut (0.9651).
- **The middle of a part is taken out only where our solid is open round a listed axis and the
  views take the cut** (2026-11-12, user: build the core step, after practice 18). The true
  volume is only the upper limit; the judge chooses. Of the cylinders the judge accepts the
  widest is kept, not the best rated: every one of them fits the views better than the filled
  solid, and the judge turns against the wider ones for the flat sides of our slabs, not for
  the part. One part stands behind this.
- **The ends of such a part are where our solid is closed all the way round at the rim of the
  listed cylinder** (2026-11-12). Following it nearer the axis took the first turns of the coil
  for the end plate (a core 17 mm long instead of 27), and a majority of the lines round the
  axis found no gap at all.
- **A part whose volume and surface say "thin shell" is hollowed** (2026-11-12, user: build the
  shell step, after practice 19). No view shows the inside, so the wall is solved from the true
  volume and the result checked by the true surface. It breaks "the solid contains the part" on
  purpose: a filled shell overlaps the part by about a sixth. The limits (twice the volume, 0.4
  to 0.65 of the surface) rest on one part; the 43 other stored solids do not meet them.
- **The inside is found by moving the solid along the axes and intersecting, not by an offset**
  (2026-11-12): OpenCascade's offset of the 421-face solid fails, the six intersections take 8 s.
  The wall is then thinner across the diagonals than along the axes; 14 directions took 19 s and
  changed the volume by 5 %, which the correction of the wall makes up anyway.
- **Cavities and the deeper search under floors went in without an eval run** (2026-11-12, user:
  build them, run no eval case). They rest on the census of the excess and on the practice
  parts' volumes only; the 24 parts have to be run before these are trusted.
- **A cavity is read as detail inside a face that our solid has flat**, not as a void no view
  shows (2026-11-12, this session's reading of the user's "cavities"). An enclosed void has no
  evidence in the views and a wrong one always costs overlap.
- **Level faces take their flat areas from the drawing** (2026-11-12, user). On the 24 parts
  sampled IoU 0.8945 to 0.8957 and 295 to 288 faces a solid; eval 27 and 38 up, eval 99 no
  longer cut by the step.
- **The facing tone is the commonest flat tone near the table's tone for a face square to the
  camera** (2026-11-12), not the commonest flat tone: on practice 9 the banks fill the views and
  the commonest was a dark slanted face, so no plane was found there at all.
- **A level counts as sure only from 10 000 px**: below that the level that stands out was wrong
  for a quarter of the faces.
- **Plane faces cut only where the isometric views give a sure plane, right after level faces**
  (2026-11-12, user: "first only with the sure faces"). The solid must stand more than 4 px in
  front of the plane: a level read off the pictures is right to 3 px, and at 1.5 px the step
  shaved the top and bottom of practice 1 by 2 px (27 faces for 19).
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
- **The model is asked where, not how deep** (2026-11-12, measurement in `state.md`). The served
  model cannot read millimetres off the views and copies whatever numbers it is given; it can
  name the region where our solid is filled and the part is not. Depths are found by cutting and
  judging, and only in flat regions square to the view: a column has a flat floor.
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
- **Every case runs in a process of its own, several at once** (2026-11-12, user). The run used
  one of the laptop's ten processors, and a crash took the cases after it along. The process
  lives from the hull to the last step, so the solid never crosses a process boundary and the
  result is the one the single process gave, load apart.
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
- **The sources of a view are a choice in the corner of its picture** (2026-11-12, after the user
  called the left view buggy; not yet confirmed by him), drawn by the server for any case, not
  only for a run. As a second slider its labels could not be clicked and it took height from
  the picture.
- **Every case shows the same stops, whether a step ran or not** (2026-11-12, user).
- **A new step needs no UI change**: `with trace.step(...)` makes a stop. For the stop to exist
  before the step has run, add its trace name to `STEP_NAMES` in `hull_agent.py`; for old traces,
  to `STOPS` in `ui/static/app.js`; its one-line description to `ABOUT` there.
- **Tracing is off in the Docker image** (`ENV TRACE=0`).

## Repo

- **First submission 2026-11-12 without the model, and `main` takes this branch's tree** (user).
  The endpoint is a tunnel on one laptop, which the image cannot reach. `main` held a teammate's
  other pipeline; it was merged with `-s ours` so that nothing of this branch changed.

- **Feature branches; merge to `main` only to submit, when the user says so** (2026-11-11).
