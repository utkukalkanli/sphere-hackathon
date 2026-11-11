"""The deterministic part of the pipeline. No model is called.

Step 1 (visual_hull): the bounding box carved by the silhouettes of the views.
Step 2 (holes): the cylindrical holes listed in facts.json cut out of it.
"""

import os
import tempfile
from pathlib import Path

from agentic_cad import holes as hl
from agentic_cad import trace
from agentic_cad import visual_hull as vh

# A cut that leaves less than this share of the part's true volume removed real material: undo it
MIN_VOLUME_SHARE = 0.97
# Seconds one cut may take before it is abandoned and the previous solid kept. The whole run over all
# test cases has a 20 minute limit; the straight cuts normally take 1-3 s, the diagonal one 5-40 s.
CUT_TIMEOUT = float(os.environ.get("HULL_CUT_TIMEOUT", 30))
ISO_CUT_TIMEOUT = float(os.environ.get("HULL_ISO_CUT_TIMEOUT", 40))
HOLES_TIMEOUT = float(os.environ.get("HULL_HOLES_TIMEOUT", 40))


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

    def close(self) -> None:
        self.cutter.close()

    def generate_cad(self, prompt: str, views: list[Path], context: Path | None, output_path: Path) -> None:
        case_dir = views[0].parent.parent if views else None
        solid = self.build_hull(prompt, views)
        if self.use_holes:
            try:
                solid = self.cut_holes(solid, case_dir)
            except Exception as error:  # the hull is still a valid answer
                trace.log(f"cut_holes raised {error!r}; keeping the hull", level="error")
        with trace.step("export_step", kind="tool", input={"output_path": str(output_path)}) as step:
            solid = vh.tidy(solid)
            vh.write_step(solid, output_path)
            step.metric(volume_cm3=round(vh.volume(solid) / 1000, 3))

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
        self.volumes, self._stage = {}, 0

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

        for name in [*vh.ORTHO, *(["iso"] if self.use_iso else [])]:
            with trace.step(f"cut_{name}", kind="code") as step:
                try:
                    carved = self._cut_direction(step, name, views, dims, solid, target)
                except Exception as error:  # one direction failing must not lose the others
                    step.fail(f"this direction raised {error!r}; keeping the previous solid")
                    continue
                if carved is not None:
                    solid = carved
                    self._record(step, solid, name)
        return solid

    def _cut_direction(self, step, name: str, views: list[Path], dims: dict, solid, target: float | None):
        """The solid cut along one direction, or None when there is nothing usable to cut with."""
        if name == "iso":
            silhouette = vh.iso_silhouette(views, dims, solid)
        else:
            silhouette = vh.ortho_silhouette(name, views, dims)
        if silhouette is None:
            step.fail("no usable silhouette for this direction; nothing cut")
            return None
        holes = sum(len(o["holes"]) for o in silhouette.outlines)
        points = sum(len(o["outer"]) + sum(len(h) for h in o["holes"]) for o in silhouette.outlines)
        step.log(f"views {' + '.join(silhouette.views)}: {len(silhouette.outlines)} outline(s), "
                 f"{holes} hole(s), {points} points")
        if silhouette.info:
            step.log(silhouette.info)
        step.attach(f"silhouette_{name}.png", vh.preview(silhouette))
        timeout = ISO_CUT_TIMEOUT if name == "iso" else CUT_TIMEOUT
        return self._guarded(step, solid, target, lambda: self.cutter.cut(solid, silhouette, timeout))

    # -- step 2 --------------------------------------------------------------------------------

    def cut_holes(self, solid, case_dir: Path | None):
        with trace.step("cut_holes", kind="code") as step:
            target = vh.read_volume(case_dir)
            holes = hl.read_holes(case_dir)
            step.metric(holes=len(holes))
            if not holes:
                step.log("facts.json lists no cylindrical holes")
                return solid
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

    def _guarded(self, step, solid, target: float | None, operation, quiet: bool = False):
        """Run a cut; return the new solid, or None (previous one is kept) when it fails, times out
        or would leave less material than the part really has."""
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
        if target and after < MIN_VOLUME_SHARE * target:
            message = (f"the cut would leave {after:.0f} mm3, less than the part's {target:.0f} mm3; "
                       "keeping the previous solid")
            step.log(message, level="warning") if quiet else step.fail(message)
            return None
        step.metric(volume_cm3=round(after / 1000, 3), removed_cm3=round((before - after) / 1000, 3),
                    **({"vs_true_volume": round(after / target, 3)} if target else {}))
        step.set_output({"volume_mm3": round(after, 2), "removed_mm3": round(before - after, 2)})
        return carved
