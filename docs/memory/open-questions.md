# Open questions

Remove a question when it is answered and record the answer in `decisions.md`, `state.md` or
`knowledge.md`. Older wording: `git show bda2104:docs/memory/open-questions.md`.

## Submission (the user decides)

- **Where is the leaderboard, and what did commit `fdcc8c6` score?** (2026-11-12.) The first
  submission is in; the README gives no address. A score per part would also answer the next
  question.
- **What happens to the teammate's pipeline that was on `main`?** (2026-11-12.) It is in the
  history (`70b1a15`), not in the tree. Whether parts of it come back (the vLLM serving script,
  the Brev guides) is for the user and the teammate.
- **What does the organisers' score measure, and how does it treat an enclosed void?** We assume
  volume overlap. If it is surface based or counts faces, polygonal outlines and extra faces cost
  more than we think. A first submission with a score per part would show it: practice 6 near 0.3
  means volume, 0.55 or more a surface or composite score. Carving the core of a shell and the
  value of the split step both wait on this.
- **How fast is the organisers' machine?** Every case has a file whatever happens, but a cut
  that times out or a case that runs out of its share there gives another solid than here. The
  image has not been run since phase 0, and it already differed from the laptop on practice 6, 7
  and 9. Emulated (3 to 4 times slower) practice 7 would be stopped by its share.
- **A kill in the first second or during a rename.** Before the boxes are written (0.7 to 1.1 s)
  there are fewer than 9 files; a `.test_case_<n>.stp.part` can be left behind in the
  milliseconds of a rename. What the organisers do with an extra file is not known.
- **How many processors and how much memory does the organisers' machine have?** The README
  gives only the GPU. Cases now run side by side on all processors but one; each process holds
  OpenCascade and its case's pictures. On a machine with fewer real cores than it reports, cuts
  time out that do not here. `HULL_WORKERS=1` is the old order.

## The model

- **Which model, and is the endpoint public and up until judging ends?** A teammate serves one
  on the Brev GPU (branch `feat/llm-cad-agent`). `OPENAI_BASE_URL`, `OPENAI_API_KEY` and
  `OPENAI_MODEL` are set nowhere. Unmeasured: the time of one request.
- **Does a served model's proposal raise the overlap?** Needs the endpoint, a hard set with
  ground truth (`tools/find_hard.py`: hull and holes on all 266 parts, needs the user's go), and
  the test: mean IoU up, no part down by more than 0.01, with and without telling the model the
  excess.
- **How does a judge tell a right cut with an uncut neighbour from a wrong cut?** The local rule
  refuses 15 of 27 right cuts because the walls a cut leaves against remaining excess count
  against it. Excusing new walls leaves a column cut in a facing area with no tone evidence at
  all. Judging cuts in groups is untried.
- **Is 0.2 % of a view's part the right outline tolerance?** It lets 156 px through on practice 5.
- **What should read the evidence table?** Its flags (lines under our surface, coplanar facing
  neighbours) are used by nothing; how right they are against the ground truth is unmeasured.

## Depth and shape

- **Depth of blind recesses**, the largest loss. Still not found: floors no isometric camera
  sees (eval 38 inside the channel before the supported cuts; deep or narrow pockets), small
  floors, floors only partly in sight (eval 236, 171), slanted faces with no neighbour of known
  depth (practice 9 banks), curved floors.
- **Depth where the pictures alone do not give it** (2026-11-12, the user: depth matters most,
  and knowing where a surface does not continue). The pictures give planes for the axis faces
  whose level stands out and for a few slanted faces between two of them; nothing for curved
  faces, small faces or a face behind a border. Whether a border is a meeting or a hiding edge
  is what is missing, and the T-junctions alone do not settle it (81 %). Untried: our solid's
  depth jump across a border; a hiding edge taken as certain where two faces of one axis lie
  side by side or a border between two axis faces does not run along the third axis; why a
  pocket floor's own edges score lower than its rim's (eval 110); a slanted face on one known
  border with its angle from the tone; a depth map that is our solid's depth where tone and
  lines confirm it; a monocular depth network only to choose between candidates.
- **The time of the recess step.** (2026-11-12.) It is bounded by 6 faces or pairs a case, not by
  a clock: eval 236 went from 26 to 203 s, 38 from 29 to 117 s, the nine practice parts from 228
  to 456 s (cases in parallel). On a slower machine the case shares decide what is searched. The
  cuts on practice 7 and 8 have not been looked at; the same reading in the isometric views is
  untried (there a level solid across one tone is a step for 13 of 32).
- **What is wrong with the lines on complex parts?** (2026-11-12.) The user finds them bad
  where lines are a few pixels apart or faint. Measured: faces narrower than 4 px are lost and
  neither a larger picture nor fainter lines brings them back (`knowledge.md`); the information
  is not in a 1024 px view. Untried: putting in what is known from elsewhere, such as circles
  and fillets with the radii of `facts.json` and the edges our solid and the found planes
  predict. Which case and view the user means is still not known.
- **Which steps move onto the drawing?** (2026-11-12.) `drawing.py` gives closed faces and fitted
  edges; the sources, level faces and plane faces use it. `regions.py`, `lines.py`, shading, pockets,
  split and the verifier still read the line pixels. Each move changes what those steps
  cut and needs the 24 parts. Also open in the drawing itself: faces a few pixels wide, lines
  that fade for more than 2 px (the gap stays open and the faces join), and whether the user
  finds it sharp enough now (not yet seen by him in the dashboard).
- **Plane faces beyond the sure ones.** (2026-11-12.) The step uses only faces of at least
  10 000 px whose level stands out, and cuts on 2 of the 24 eval parts. Open: telling a floor's
  own edge from the rim it is seen past (levels that stand out on areas with excess: 16 of 53
  right); whether a monocular depth network helps to choose there; a check that the straight
  view really sees the face (practice 9, `knowledge.md`), which only the volume guard does now;
  on practice 9, whether its cut or the pockets cut it displaces is right.
- **Eval 1 still has 14 faces of 7**: the silhouette step leaves the r 19 cylinder in four pieces
  and two flats where the arc touches the box.
- **Tones into geometry.** Agreed approach: classify regions by tone and fit primitives whose
  sizes are in `facts.json`. Open: how a region gets its depth when it touches no face of known
  depth; a slanted face the solid does not have (12 % of the slanted area is still called
  facing); what to do with flat areas the isometric view does not confirm, which mark where the
  solid is wrong.
- **Is the organisers' tone table good enough?** It was measured on our own solids of the 9
  practice parts and fills 692 of about 1300 directions. A wrong table makes `curved.py` drop
  right proposals; it cannot make it cut.
- **Grooves between fins** (practice 6). Done for flat faces standing on a visible level face.
  Open: flanks seen only from the side, banks that stand on nothing visible, small areas on
  thin-walled parts where the edge-on line is found by chance (eval 99).
- **Hollow parts** (practice 6; the user asked for a step). Volume and surface cannot trigger it
  (`knowledge.md`). A step would wait until the views fit everywhere and volume is still missing,
  then offset the outside inwards by a wall solved from the volume. Open: the grooves come
  first; whether the wall is even; how the scorer counts a void. Test parts can be made by
  hollowing a ground truth. Not built.
- **Features that stand only over part of the height** (practice 8 bolt lobes). The hull keeps
  them as full prisms. Idea, not built: a silhouette cut restricted to a region, with a contour
  drawn inside a perpendicular view as its profile.
- **Curved faces that are not rings around a listed axis**: fillets along straight edges,
  cylindrical pockets seen from the side, tilted axes. Untouched.
- **Nothing checks a round cut against the views.** On practice 6 a "disc" cut came back with
  the section still square and was kept. `rounds.py` and `curved.py` have no projection check.
- **How deep is a blind hole listed only as a span?** Half the room is a guess. Bounds on
  practice 8: deeper than 0.7 diameters; 10.1 mm of room for the d7 holes. Worth up to 3.7 of its
  8.1 cm3 of excess. Could the true volume fix it once the rest is right?
- **The face of a single mouth.** Nothing in a view tells a mouth flush with a face from one at
  the bottom of a narrow counterbore; `facts.json` lists the wider coaxial cylinder or cone in
  that case, unused.
- **Pockets from lines: what is left?** Figures on thin-walled parts that do not close (eval 51,
  99); a figure closed straight across under a curved rim leaves a sliver; rings around listed
  holes with the cone angles of `facts.json` (eval 123), untried.
- **Isometric support from 1.05 instead of 1.5 times the true volume?** (Put off by the user.)
  Hull + holes IoU 0.8541 to 0.8574, none lower, but practice 2 and 4 lose the square sections
  the rounds step looks for. Not run through the whole pipeline. Also open: accepting the cut
  per region by the views' lines and tones instead of as a whole.
- **The rest of `facts.json`** (user, 2026-11-11): arc and fillet radii, cone angles, counts.
  The research round puts what they address at about 2 % of the excess; counts could still check
  a candidate.
- **The 97 % volume guard is a refusal on the part's total.** On a part whose volume is right
  for a wrong reason (practice 8 before the blind holes) it refuses right cuts.
- **`context.png` is unused.** Anything in it worth using? Absent for some parts.

## Housekeeping

- README.md's dashboard section is out of date.
- `tools/dataset` needs `cadquery` and `pyvista`, which are only in the local `datasets/.venv`.
- `.idea/` and `.impeccable/` are untracked: add them to `.gitignore`?
- `docs/` and `CLAUDE.md` go into the Docker image; exclude them in `.dockerignore`?
