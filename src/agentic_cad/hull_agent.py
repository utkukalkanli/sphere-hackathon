"""The deterministic part of the pipeline. No model is called.

Step 1 (visual_hull): the bounding box carved by the silhouettes of the views.
Step 2 (holes): the cylindrical holes listed in facts.json cut out of it.
"""

import os
import tempfile
import time
from pathlib import Path

from agentic_cad import bowls
from agentic_cad import cavities
from agentic_cad import holes as hl
from agentic_cad import mouths
from agentic_cad import spans
from agentic_cad import iso_support
from agentic_cad import outline_fit as of
from agentic_cad import pockets
from agentic_cad import propose
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy

from agentic_cad import curved
from agentic_cad import datums
from agentic_cad import depth
from agentic_cad import evidence as evidence_table
from agentic_cad import export
from agentic_cad import rounds
from agentic_cad import shading
from agentic_cad import split
from agentic_cad import trace
from agentic_cad import visual_hull as vh

# A cut that leaves less than this share of the part's true volume removed real material: undo it
MIN_VOLUME_SHARE = 0.97
# A silhouette cut may go this far below it, as a last resort, when its result matches the view it
# was cut from: then nothing is wrong with the cut, the outline is a pixel tight on a thin part, and
# leaving the direction uncut costs far more (eval 75: overlap 0.67 uncut, 0.92 cut)
TIGHT_VOLUME_SHARE = float(os.environ.get("HULL_TIGHT_SHARE", 0.90))
# The isometric views are never carved with on their own. When the three straight cuts leave more
# than this many times the true volume (facts.json), the box is carved again with the isometric
# silhouette first, its scale confirmed on the hull of the straight cuts. The result must still fill
# all three straight silhouettes, and what it removes is judged against the lines of the 8 views,
# as a whole or piece by piece (iso_support.py). A large value switches it off.
ISO_EXCESS = float(os.environ.get("HULL_ISO_EXCESS", 1.5))
# ... and it must take away at least this share of the true volume: less is not worth the slanted
# faces it leaves (eval 74 and 123: twice the faces for 0.1 to 0.2 % of the volume)
ISO_MIN_GAIN = float(os.environ.get("HULL_ISO_MIN_GAIN", 0.01))
# HULL_ISO_PIECES=0: the result is taken or left as a whole, without asking the views
ISO_PIECES = os.environ.get("HULL_ISO_PIECES", "1") != "0"
# ... and only while the case has used less than this many seconds
ISO_BUDGET = float(os.environ.get("HULL_ISO_BUDGET", 40))
# Seconds one cut may take before it is abandoned and the previous solid kept. The whole run over all
# test cases has a 20 minute limit; the straight cuts normally take 1-3 s, the diagonal one 5-40 s.
CUT_TIMEOUT = float(os.environ.get("HULL_CUT_TIMEOUT", 30))
ISO_CUT_TIMEOUT = float(os.environ.get("HULL_ISO_CUT_TIMEOUT", 40))
HOLES_TIMEOUT = float(os.environ.get("HULL_HOLES_TIMEOUT", 40))
# Cutting again in another order is only tried while a case has used less than this many seconds
RETRY_BUDGET = float(os.environ.get("HULL_RETRY_BUDGET", 20))


# Steps after the holes, in order; HULL_SKIP=bowls,rounds,curved,cavities,depth,model,shading,pockets,split switches some off.
# "model" (propose.py) does nothing unless an endpoint is configured; it comes before the fine cuts,
# because a large structural cut lowers the precision every later cut needs.
# "split" moves no material and must stay last: a later cut or merge would undo it.
LATER_STEPS = {"bowls": bowls, "rounds": rounds, "curved": curved, "cavities": cavities, "depth": depth, "model": propose, "shading": shading,
               "pockets": pockets, "split": split}
SKIP_STEPS = {name for name in os.environ.get("HULL_SKIP", "").split(",") if name}
# The name each later step gives its trace step, for the plan the dashboard shows before anything has run
STEP_NAMES = {"bowls": "cut_bowls", "rounds": "cut_rounds", "curved": "cut_curved", "cavities": "cut_cavities", "depth": "level_faces", "model": "model_cuts",
              "shading": "shading_depth", "pockets": "cut_pockets", "split": "split_faces"}


class VisualHullAgent:
    # The isometric cut is off: measured on the eval set it adds under one point of overlap, is by far
    # the slowest cut, and leaves slanted faces that are not features of the part. Its views are better
    # used to check a candidate solid than to carve one (vh.iso_silhouette has the scale worked out).
    def __init__(self, use_iso: bool = False, use_holes: bool = True):
        self.use_iso = use_iso
        self.use_holes = use_holes
        self.cutter = vh.Cutter()
        self.volumes: dict[str, float] = {}  # volume in mm3 after each step of the last case
        self._stage = 0
        self.deadline: float | None = None  # time.time() after which the case's later steps are skipped
        self._iso = None  # an isometric silhouette whose scale is already confirmed, for _cut_direction
        self.keep_faces = False  # set by the split step: the export must not merge its faces again
        self.lines_found = None  # handed from the pockets step to the split step: the lines of an unchanged solid
        self._evidence = None  # what the inputs of the case in hand say (evidence.py)
        self._output_path: Path | None = None  # where `refine` writes the case's answer, for `save`
        self._sound: bool | None = None  # whether the solid so far reads back valid from its file, if asked

    def evidence(self, case_dir: Path, dims: dict | None = None) -> "evidence_table.Evidence":
        """The views of a case and what the steps derive from them, read once for the case."""
        if self._evidence is None or self._evidence.case_dir != case_dir:
            self._evidence = evidence_table.Evidence(case_dir, dims or dict(zip("xyz", vh.read_dimensions(case_dir))))
        return self._evidence

    def _show_evidence(self, solid, case_dir: Path | None) -> None:
        """For the dashboard only: the regions of the views with what the solid has under them, as a
        table and as marked views. On a copy of the solid, so that nothing here can reach a step."""
        if case_dir is None or trace.current_case().dir is None:
            return
        with trace.step("evidence", kind="io", description=(
                "The areas between the lines of each view, numbered, with what the solid has under them")) as step:
            try:
                table = self.evidence(case_dir)
                rows = table.rows(BRepBuilderAPI_Copy(solid, True, False).Shape())
                step.attach("regions.json", rows)
                for view, picture in table.marked().items():
                    step.attach(f"regions_{view}.png", picture)
                step.metric(regions=len(rows), with_lines_below=sum(1 for row in rows if row["below_px"] > 0),
                            coplanar=sum(1 for row in rows if row["coplanar_with"]))
            except Exception as error:
                step.log(f"raised {error!r}; the steps do not need it", level="warning")

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.time() > self.deadline

    def close(self) -> None:
        self.cutter.close()

    def generate_cad(self, prompt: str, views: list[Path], context: Path | None, output_path: Path) -> None:
        """One case from start to end. scripts/main.py runs the same three calls, but for all cases
        in turn: every hull first, then the later steps."""
        case_dir = views[0].parent.parent if views else None
        solid = self.carve(prompt, views)
        self.checkpoint(solid, output_path)
        solid = self.refine(solid, case_dir, views, output_path=output_path)
        self.finish(solid, output_path)

    def carve(self, prompt: str, views: list[Path]):
        """Steps 1 and 2: the hull with the listed holes cut."""
        case_dir = views[0].parent.parent if views else None
        trace.plan(self.planned())
        solid = self.build_hull(prompt, views)
        if self.use_holes:
            try:
                solid = self.cut_holes(solid, case_dir)
            except Exception as error:  # the hull is still a valid answer
                trace.log(f"cut_holes raised {error!r}; keeping the hull", level="error")
            try:
                solid = mouths.apply(self, solid, case_dir)
            except Exception as error:
                trace.log(f"mouths.apply raised {error!r}; keeping the solid as it was", level="error")
        return solid

    def checkpoint(self, solid, output_path: Path | None) -> bool:
        """Make `solid` the case's answer on disk, if its file reads back as one valid solid
        (export.checkpoint). Remembers the outcome for `refine`."""
        self._sound = export.checkpoint(solid, output_path, self.keep_faces)
        if not self._sound:
            trace.log("the solid does not survive being written and read; the file is left as it was", level="warning")
        return self._sound

    def save(self, solid) -> bool:
        """For a step that accepts several cuts one after another: make `solid` the case's answer now
        (export.checkpoint, onto the file `refine` was given). False: its file does not read back as
        one valid solid, and the step should drop that cut."""
        saved = export.checkpoint(solid, self._output_path, self.keep_faces)
        if saved:
            self._sound = True
        return saved

    def state(self) -> dict:
        """What a case needs back (`resume`) when its later steps run after other cases' hulls."""
        return {"volumes": dict(self.volumes), "stage": self._stage, "sound": self._sound}

    def resume(self, state: dict) -> None:
        self.volumes, self._stage, self._sound = dict(state["volumes"]), state["stage"], state["sound"]

    def finish(self, solid, output_path: Path) -> None:
        """Closes the case in the trace. The file is already there: every step that changed the
        solid wrote it."""
        with trace.step("export_step", kind="tool", input={"output_path": str(output_path)}) as step:
            step.metric(volume_cm3=round(vh.volume(solid) / 1000, 3))
            if not Path(output_path).is_file():
                step.fail("no file was written for this case")

    def planned(self) -> list[dict]:
        """Every step a case goes through, in order; "off" says why one is switched off for this run."""
        steps = [{"name": "bounding_box"}, *({"name": f"cut_{name}"} for name in vh.ORTHO)]
        steps.append({"name": "cut_iso"} if self.use_iso else {"name": "cut_iso", "off": "the isometric cut is switched off"})
        steps.append({"name": "isometric_support", "off": "the isometric cut runs as a cut of its own"} if self.use_iso
                     else {"name": "isometric_support"})
        steps.append({"name": "check_views"})
        steps.append({"name": "cut_holes"} if self.use_holes else {"name": "cut_holes", "off": "the hole step is switched off"})
        if not self.use_holes:
            steps.append({"name": "open_mouths", "off": "the hole step is switched off"})
        elif not mouths.ENABLED:
            steps.append({"name": "open_mouths", "off": "switched off with HULL_MOUTHS=0"})
        else:
            steps.append({"name": "open_mouths"})
        steps.append({"name": "evidence"})
        for name in LATER_STEPS:
            step = {"name": STEP_NAMES.get(name, name)}
            if name in SKIP_STEPS:
                step["off"] = f"switched off with HULL_SKIP={name}"
            elif name == "model" and not propose.enabled():
                step["off"] = "no model endpoint is configured (OPENAI_BASE_URL, OPENAI_MODEL)"
            steps.append(step)
        return [*steps, {"name": "export_step"}]

    def refine(self, solid, case_dir: Path | None, views: list[Path], skip: tuple[str, ...] = (),
               output_path: Path | None = None):
        """The steps after the holes, each in its own module with an `apply(agent, solid, case_dir,
        views)`. A step that raises is skipped: the solid so far is still a valid answer.

        After every step that changed the solid its file is written and read back
        (export.checkpoint; onto `output_path` if given, so a run that is killed keeps the steps
        done so far). A step whose solid does not survive that is dropped, it alone."""
        self.keep_faces, self.lines_found = False, None
        self._output_path = output_path
        self._show_evidence(solid, case_dir)
        names = [name for name in LATER_STEPS if name not in skip and name not in SKIP_STEPS]
        for index, name in enumerate(names):
            module = LATER_STEPS[name]
            if self.out_of_time():
                trace.log(f"the case has used its share of the run's time; {name} and later steps are skipped", level="warning")
                for late in names[index:]:
                    trace.skipped(STEP_NAMES.get(late, late), "the case had used its share of the run's time")
                break
            kept = (self.keep_faces, dict(self.volumes))
            try:
                result = module.apply(self, solid, case_dir, views)
            except Exception as error:
                trace.log(f"{name} raised {error!r}; keeping the solid as it was", level="error")
                self.keep_faces, self.volumes, self.lines_found = *kept, None
                continue
            if result is None or result is solid:
                continue
            if export.checkpoint(result, output_path, self.keep_faces):
                solid, self._sound = result, True
                continue
            if self._sound is None:  # nobody has asked yet whether the solid before this step survives
                self._sound = export.checkpoint(solid, None, kept[0])
            if self._sound:
                trace.log(f"the solid {name} made does not survive being written and read; the step is dropped",
                          level="error")
                self.keep_faces, self.volumes, self.lines_found = *kept, None
            else:  # not this step's fault: go on with it, a later step may still give a file that reads
                trace.log(f"neither the solid before {name} nor the one after it survives being written and "
                          "read; the file is left as it was", level="warning")
                solid = result
        return solid

    def _record(self, step, solid, label: str) -> None:
        """Note the volume after this step and, when tracing, save the solid for the dashboard."""
        self.volumes[label] = vh.volume(solid)
        if trace.current_case().dir is None:
            return
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"stage_{self._stage}_{label}.stp"
                vh.write_step(solid, path)
                step.attach_file(path, role="stage")
        except Exception as error:  # only the dashboard misses this stage
            step.log(f"could not save the stage: {error!r}", level="warning")
        self._stage += 1

    # -- step 1 --------------------------------------------------------------------------------

    def build_hull(self, prompt: str, views: list[Path]):
        case_dir = views[0].parent.parent if views else None
        self.volumes, self._stage, self._sound, self.keep_faces = {}, 0, None, False

        with trace.step("bounding_box", kind="io") as step:
            x, y, z = vh.read_dimensions(case_dir, prompt)
            dims = {"x": x, "y": y, "z": z}
            target = vh.read_volume(case_dir)
            solid = vh.box(dims)
            box_volume = x * y * z
            step.metric(volume_cm3=round(box_volume / 1000, 3))
            step.set_output({"x_mm": x, "y_mm": y, "z_mm": z, "box_volume_mm3": round(box_volume, 2),
                             "true_volume_mm3": target})
            self._record(step, solid, "box")

        names = [*vh.ORTHO, *(["iso"] if self.use_iso else [])]
        started = time.time()
        box = solid
        solid, silhouettes, troubled = self._carve(box, names, views, dims, target)

        # A direction whose fitted outline could not be cut often can be when it is cut first, before
        # the other directions have left curved faces for it to run into.
        for first in [n for n in troubled if n in vh.ORTHO]:
            if time.time() - started > RETRY_BUDGET:
                break
            with trace.step(f"retry_{first}_first", kind="other") as step:
                order = [first] + [n for n in names if n != first]
                kept = dict(self.volumes)
                again, again_silhouettes, again_troubled = self._carve(box, order, views, dims, target)
                if again_troubled:
                    self.volumes = kept
                    step.fail(f"still not clean for {again_troubled}; keeping the first result")
                    continue
                solid, silhouettes, troubled = again, again_silhouettes, []
                step.log("every direction cut cleanly in this order; using this solid")
                break

        if self.use_iso:
            pass
        elif not target or vh.volume(solid) <= ISO_EXCESS * target:
            trace.skipped("isometric_support", f"the straight cuts left no more than {ISO_EXCESS:g} times the true volume")
        elif self.out_of_time() or time.time() - started >= ISO_BUDGET:
            trace.skipped("isometric_support", "the case had no time left for it")
        else:
            solid, silhouettes = self._with_iso(box, solid, silhouettes, troubled, names, views, dims, target)

        with trace.step("check_views", kind="check") as step:
            worst = self._check(step, solid, silhouettes)
            if worst is not None and worst > vh.CHECK_AREA and vh.OUTLINE != "pixels":
                # Something is off that no single cut showed: rebuild with the outlines as traced
                # (flat faces only, which OpenCascade cuts reliably) and keep whichever agrees better
                step.log("rebuilding with the traced outlines", level="warning")
                kept = dict(self.volumes)
                traced, traced_silhouettes, _ = self._carve(box, names, views, dims, target, mode="pixels")
                traced_worst = self._check(step, traced, traced_silhouettes)
                if traced_worst is not None and traced_worst < worst:
                    solid, worst = traced, traced_worst
                else:
                    self.volumes = kept
                if worst > vh.CHECK_AREA:
                    step.fail("the solid does not match its silhouettes")
            if worst is not None:
                step.metric(worst_mismatch=round(worst, 5))
        return solid

    def _with_iso(self, box, solid, silhouettes: list, troubled: list, names: list[str], views: list[Path],
                  dims: dict, target: float):
        """Carve the box again with the isometric silhouette first (on the box that cut is cheap; after
        the straight cuts it can take minutes). Kept only if every cut went through as cleanly as
        before, less is left, and the solid still fills the three straight silhouettes."""
        with trace.step("isometric_support", kind="code", description=(
                "The straight cuts left more than the true volume: what the isometric silhouette would "
                "also remove goes where the lines of the views agree, as a whole or piece by piece")) as step:
            try:
                self._iso = vh.iso_silhouette(views, dims, solid)
                if self._iso is None:
                    step.log("the scale of the isometric views could not be confirmed on the hull; nothing done")
                    return solid, silhouettes
                step.log(self._iso.info)
                kept, before = dict(self.volumes), vh.volume(solid)
                again, again_silhouettes, again_troubled = self._carve(box, ["iso", *names], views, dims, target)
                after = vh.volume(again)
                straight = [s for s in again_silhouettes if s.name != "iso"]
                worst = self._check(step, again, straight)
                self.volumes = kept
                if (len(again_troubled) > len(troubled) or len(straight) < len(silhouettes)
                        or before - after < ISO_MIN_GAIN * target or (worst is not None and worst > vh.CHECK_AREA)):
                    step.log(f"not kept: {after / target:.3f} of the true volume against {before / target:.3f}, "
                             f"unclean cuts {again_troubled}, worst mismatch {worst}")
                    return solid, silhouettes
                if ISO_PIECES:
                    case_dir = views[0].parent.parent
                    again = iso_support.choose(self, step, solid, again, case_dir, dims, target)
                    if again is None:
                        step.log("the views support none of it; keeping the hull of the straight cuts")
                        return solid, silhouettes
                    after = vh.volume(again)
                    if before - after < ISO_MIN_GAIN * target:
                        step.log("what the views support is too little to be worth its faces; keeping the hull")
                        return solid, silhouettes
                step.metric(volume_cm3=round(after / 1000, 3), removed_cm3=round((before - after) / 1000, 3),
                            vs_true_volume=round(after / target, 3))
                self._record(step, again, "iso")
                return again, straight
            except Exception as error:
                step.log(f"raised {error!r}; keeping the hull of the straight cuts", level="warning")
                return solid, silhouettes
            finally:
                self._iso = None

    def _check(self, step, solid, silhouettes: list) -> float | None:
        """Largest disagreement between the solid's projection and any silhouette it was cut with."""
        worst = None
        for silhouette in silhouettes:
            try:
                error = vh.projection_error(solid, silhouette)
            except Exception as failure:
                step.log(f"could not check {silhouette.name}: {failure!r}", level="warning")
                continue
            if error is None:
                continue
            step.log(f"{silhouette.name}: {error[0]:.4f} outside the silhouette, {error[1]:.4f} of it not covered")
            worst = max(worst or 0.0, *error)
        return worst

    def _carve(self, box, names: list[str], views: list[Path], dims: dict, target: float | None,
               mode: str | None = None):
        """Cut `box` along `names` in this order. Returns the solid, the silhouettes that were cut, and
        the directions that did not cut cleanly with their fitted outline."""
        solid, silhouettes, troubled = box, [], []
        aligned = self._aligned(names, views, dims) if (mode or vh.OUTLINE) != "pixels" else {}
        for name in names:
            with trace.step(f"cut_{name}", kind="code") as step:
                try:
                    carved, silhouette, clean = self._cut_direction(step, name, views, dims, solid, target, mode,
                                                                    aligned.get(name))
                except Exception as error:  # one direction failing must not lose the others
                    step.fail(f"this direction raised {error!r}; keeping the previous solid")
                    troubled.append(name)
                    continue
                if not clean:
                    troubled.append(name)
                if carved is not None:
                    solid = carved
                    silhouettes.append(silhouette)
                    self._record(step, solid, name)
        return solid, silhouettes, troubled

    def _aligned(self, names: list[str], views: list[Path], dims: dict) -> dict:
        """The straight silhouettes with their outlines on coordinates shared by all three (datums.py),
        for the directions where that changed anything. Empty when it is switched off or fails."""
        if not datums.ON:
            return {}
        case_dir = views[0].parent.parent if views else None
        try:
            aligned, report = datums.aligned_silhouettes(views, dims, names, lambda name: hl.outline_hints(
                case_dir, vh.ORTHO[name]["u"], vh.ORTHO[name]["v"], vh.ORTHO[name]["along"]))
        except Exception as error:
            trace.log(f"datums raised {error!r}; cutting with the outlines as fitted", level="warning")
            return {}
        if report:
            trace.log(f"shared datums: {report['merged']} of {report['datums']} coordinates merged, "
                      f"{report['refused']} outline(s) left as fitted")
        return aligned

    def _cut_direction(self, step, name: str, views: list[Path], dims: dict, solid, target: float | None,
                       mode: str | None = None, aligned=None):
        """Cut along one direction. Returns (solid or None, the silhouette it was cut with, whether the
        fitted outline cut cleanly)."""
        case_dir = views[0].parent.parent if views else None

        def silhouette_for(outline_mode: str | None, tracing: str | None = None):
            if name == "iso":
                return self._iso or vh.iso_silhouette(views, dims, solid)
            spec = vh.ORTHO[name]
            hints = hl.outline_hints(case_dir, spec["u"], spec["v"], spec["along"])
            return vh.ortho_silhouette(name, views, dims, hints, outline_mode, tracing)

        tight = []  # cuts that match their view and only leave a little less than the true volume

        def attempt(silhouette, share: float = MIN_VOLUME_SHARE):
            """The cut, kept only if the result looks like the silhouette from this direction."""
            carved = self._guarded(step, solid, target, lambda: self.cutter.cut(solid, silhouette, timeout),
                                   quiet=True, share=share, refused=tight)
            if carved is None:
                if tight and tight[-1][1] is None:
                    tight[-1] = (tight[-1][0], silhouette)
                return None
            error = vh.projection_error(carved, silhouette)
            if error is not None and max(error) > vh.CHECK_AREA:
                step.log(f"the result does not match the silhouette ({error[0]:.4f} outside it, "
                         f"{error[1]:.4f} of it not covered): a wrong boolean result", level="warning")
                return None
            return carved

        silhouette = silhouette_for(mode)
        if silhouette is None:
            step.fail("no usable silhouette for this direction; nothing cut")
            return None, None, True
        loops = [loop for o in silhouette.outlines for loop in (o["outer"], *o["holes"])]
        kinds = {kind: sum(of.counts(loop)[kind] for loop in loops) for kind in ("line", "arc", "circle")}
        step.log(f"views {' + '.join(silhouette.views)}: {len(loops)} loop(s) of {kinds['line']} lines, "
                 f"{kinds['arc']} arcs, {kinds['circle']} full circles")
        step.metric(**{f"{kind}s": count for kind, count in kinds.items()})
        if silhouette.info.get("scale"):
            step.log(silhouette.info)
        step.attach(f"silhouette_{name}.png", vh.preview(silhouette))
        timeout = ISO_CUT_TIMEOUT if name == "iso" else CUT_TIMEOUT
        if aligned is not None:  # the same outline on the coordinates the three directions share
            carved = attempt(aligned)
            if carved is not None:
                step.metric(outline="shared datums")
                return carved, aligned, True
            step.log("cutting with the outline on shared datums failed; trying it as fitted", level="warning")
        carved = attempt(silhouette)
        if carved is not None:
            return carved, silhouette, True
        if name != "iso" and (mode or vh.OUTLINE) != "pixels":
            # The fitted outline could not be cut, cut too much, or gave a wrong solid: use the outline as traced
            step.log("cutting with the fitted outline failed; retrying with the traced pixels", level="warning")
            traced = silhouette_for("pixels")
            if traced is not None:
                carved = attempt(traced)
                if carved is not None:
                    step.metric(outline="traced pixels")
                    return carved, traced, False
        # Every outline left less than the true volume, but a cut that matches its view is still far
        # better than no cut
        for carved, used in (t for t in tight if t[1] is not None):  # the fitted outline first
            if target and vh.volume(carved) >= TIGHT_VOLUME_SHARE * target:
                error = vh.projection_error(carved, used)
                if error is None or max(error) <= vh.CHECK_AREA:
                    step.log(f"keeping a cut that leaves {vh.volume(carved) / target:.3f} of the true volume: "
                             "it matches its view", level="warning")
                    step.metric(outline="tight", vs_true_volume=round(vh.volume(carved) / target, 3))
                    return carved, used, used is silhouette
        step.fail("the cut gave no valid solid; keeping the previous one")
        return None, None, False

    # -- step 2 --------------------------------------------------------------------------------

    def cut_holes(self, solid, case_dir: Path | None):
        with trace.step("cut_holes", kind="code") as step:
            target = vh.read_volume(case_dir)
            holes = hl.read_holes(case_dir)
            step.metric(holes=len(holes))
            if not holes:
                step.log("facts.json lists no cylindrical holes")
                return solid
            # a listed span the views show closed is two blind holes, not one through hole
            try:
                holes, blind = spans.resolve(solid, holes, case_dir)
            except Exception as error:
                blind = []
                step.log(f"spans.resolve raised {error!r}; the holes are cut as listed")
            if blind:
                step.metric(spans_made_blind=len(blind))
                step.attach("blind_holes.json", blind)
            step.attach("holes.json", [{"diameter": 2 * h.radius, "length": h.length, "centre": h.centre,
                                        "axis": h.axis, "bosses_kept_inside": len(h.keep)} for h in holes])
            # All of them in one operation; if that takes real material away, one facts.json entry at a time
            carved = self._guarded(step, solid, target,
                                   lambda: self.cutter.run(hl.cut_holes, solid, holes, HOLES_TIMEOUT))
            if carved is None:
                step.log("cutting all holes at once was refused; trying one group at a time")
                carved, kept = solid, 0
                for group in sorted({h.group for h in holes}):
                    members = [h for h in holes if h.group == group]
                    attempt = self._guarded(step, carved, target, lambda: self.cutter.run(
                        hl.cut_holes, carved, members, HOLES_TIMEOUT / 2), quiet=True)
                    if attempt is not None:
                        carved, kept = attempt, kept + len(members)
                step.metric(holes_cut=kept)
                if kept == 0:
                    return solid
                step._status = None  # some groups went through: not a failed step
            self._record(step, carved, "holes")
            return carved

    # -- shared --------------------------------------------------------------------------------

    def _guarded(self, step, solid, target: float | None, operation, quiet: bool = False,
                 share: float = MIN_VOLUME_SHARE, refused: list | None = None):
        """Run a cut; return the new solid, or None (previous one is kept) when it fails, times out
        or would leave less material than the part really has. A valid result refused only for its
        volume is appended to `refused` as (solid, None)."""
        before = vh.volume(solid)
        try:
            carved = operation()
        except Exception as error:
            carved = None
            step.log(f"the cut raised {error!r}", level="warning")
        if carved is None:
            if not quiet:
                step.fail("the cut gave no valid solid; keeping the previous one")
            return None
        after = vh.volume(carved)
        if target and after < share * target:
            if refused is not None:
                refused.append((carved, None))
            message = (f"the cut would leave {after:.0f} mm3, less than the part's {target:.0f} mm3; "
                       "keeping the previous solid")
            step.log(message, level="warning") if quiet else step.fail(message)
            return None
        step.metric(volume_cm3=round(after / 1000, 3), removed_cm3=round((before - after) / 1000, 3),
                    **({"vs_true_volume": round(after / target, 3)} if target else {}))
        step.set_output({"volume_mm3": round(after, 2), "removed_mm3": round(before - after, 2)})
        return carved
