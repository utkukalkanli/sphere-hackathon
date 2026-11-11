"""Bounding box carved by the silhouettes of the views (step 1), then the cuts no silhouette shows:
the holes of facts.json and, with a model configured, pockets and recesses from the brief (step 2)."""

import json
import os
import tempfile
import time
from pathlib import Path

import cv2

from agentic_cad import features, llm_cuts, trace
from agentic_cad import visual_hull as vh
from agentic_cad.views import ViewSet

# A cut that leaves less than this share of the part's true volume removed real material: undo it
MIN_VOLUME_SHARE = 0.97
# Seconds one cut may take before it is abandoned and the previous solid kept. The whole run over all
# test cases has a 20 minute limit; the straight cuts normally take 1-3 s, the diagonal one 5-40 s.
CUT_TIMEOUT = float(os.environ.get("HULL_CUT_TIMEOUT", 30))
ISO_CUT_TIMEOUT = float(os.environ.get("HULL_ISO_CUT_TIMEOUT", 60))
# Seconds one boolean operation of step 2 may take, and all of step 2 of one case together
FEATURE_CUT_TIMEOUT = float(os.environ.get("FEATURE_CUT_TIMEOUT", 30))
FEATURE_BUDGET = float(os.environ.get("FEATURE_BUDGET", 60))


class VisualHullAgent:
    def __init__(self, use_iso: bool = True, use_features: bool = True):
        self.use_iso = use_iso
        self.use_features = use_features
        self.cutter = vh.Cutter()

    def close(self) -> None:
        self.cutter.close()

    def generate_cad(self, prompt: str, views: list[Path], context: Path | None, output_path: Path) -> None:
        solid = self.build(prompt, views)
        if self.use_features:
            solid = self.carve_features(prompt, views, solid)
        with trace.step("export_step", kind="tool", input={"output_path": str(output_path)}) as step:
            solid = vh.tidy(solid)
            vh.write_step(solid, output_path)
            step.metric(volume_cm3=round(vh.volume(solid) / 1000, 3))

    def build(self, prompt: str, views: list[Path]):
        case_dir = views[0].parent.parent if views else None
        stage = 0

        def record(step, solid, label: str) -> None:
            """Save the solid as it is after this step, so the dashboard can show how it was formed."""
            nonlocal stage
            if trace.current_case().dir is None:  # not tracing: nothing to show it to
                return
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"stage_{stage}_{label}.stp"
                vh.write_step(solid, path)
                step.attach_file(path, role="stage")
            stage += 1

        with trace.step("bounding_box", kind="io") as step:
            x, y, z = vh.read_dimensions(case_dir, prompt)
            dims = {"x": x, "y": y, "z": z}
            target = vh.read_volume(case_dir)
            solid = vh.box(dims)
            box_volume = x * y * z
            step.metric(volume_cm3=round(box_volume / 1000, 3))
            step.set_output({"x_mm": x, "y_mm": y, "z_mm": z, "box_volume_mm3": round(box_volume, 2),
                             "true_volume_mm3": target})
            record(step, solid, "box")

        for name in [*vh.ORTHO, *(["iso"] if self.use_iso else [])]:
            with trace.step(f"cut_{name}", kind="code") as step:
                if name == "iso":
                    silhouette = vh.iso_silhouette(views, dims, solid)
                else:
                    silhouette = vh.ortho_silhouette(name, views, dims)
                if silhouette is None:
                    step.fail("no usable silhouette for this direction; nothing cut")
                    continue
                holes = sum(len(o["holes"]) for o in silhouette.outlines)
                points = sum(len(o["outer"]) + sum(len(h) for h in o["holes"]) for o in silhouette.outlines)
                step.log(f"views {' + '.join(silhouette.views)}: {len(silhouette.outlines)} outline(s), "
                         f"{holes} hole(s), {points} points")
                if silhouette.info:
                    step.log(silhouette.info)
                step.attach(f"silhouette_{name}.png", vh.preview(silhouette))

                before = vh.volume(solid)
                try:
                    carved = self.cutter.cut(solid, silhouette, ISO_CUT_TIMEOUT if name == "iso" else CUT_TIMEOUT)
                except Exception as error:
                    carved = None
                    step.log(f"the cut raised {error!r}", level="warning")
                if carved is None:
                    step.fail("the cut gave no valid solid; keeping the previous one")
                    continue
                after = vh.volume(carved)
                if target and after < MIN_VOLUME_SHARE * target:
                    step.fail(f"the cut would leave {after:.0f} mm3, less than the part's {target:.0f} mm3; "
                              "keeping the previous solid")
                    continue
                solid = carved
                step.metric(volume_cm3=round(after / 1000, 3), removed_cm3=round((before - after) / 1000, 3),
                            **({"vs_true_volume": round(after / target, 3)} if target else {}))
                step.set_output({"volume_mm3": round(after, 2), "removed_mm3": round(before - after, 2)})
                record(step, solid, name)
        return solid

    # -- step 2 ------------------------------------------------------------------------------------

    def carve_features(self, prompt: str, views: list[Path], solid):
        case_dir = views[0].parent.parent if views else None
        deadline = time.monotonic() + FEATURE_BUDGET
        with trace.step("view_cameras", kind="check") as step:
            try:
                view_set = ViewSet(views, vh.read_dimensions(case_dir, prompt), solid)
            except Exception as error:
                step.fail(f"cannot set up the view cameras ({error!r}); step 2 skipped")
                return solid
            step.set_output({"views": [v.name for v in view_set.views]})
            if trace.current_case().dir is not None:
                for name, image in view_set.iso_overlay(solid).items():
                    step.attach(f"{name}_frame.png", cv2.imencode(".png", image)[1].tobytes())
        target = vh.read_volume(case_dir)

        holes = features.holes_from_facts(case_dir)
        with trace.step("cut_holes", kind="code", input=[c.describe() for c in holes]) as step:
            solid, kept = self.apply_cuts(solid, holes, view_set, target, step, deadline)

        if llm_cuts.configured() and time.monotonic() < deadline:
            planned = []
            with trace.step("plan_cuts", kind="llm") as step:
                try:
                    facts = json.loads((case_dir / "facts.json").read_text()) if case_dir else {}
                except Exception:
                    facts = {}
                messages = llm_cuts.build_messages(prompt, facts, kept, view_set.iso_overlay(solid))
                step.attach("request.json", json.dumps(messages, indent=1)[:200_000])
                try:
                    answer, usage = llm_cuts.call(messages)
                    step.attach("answer.txt", answer)
                    step.metric(**{k: v for k, v in usage.items() if isinstance(v, int)})
                    planned = llm_cuts.parse(answer)
                    step.set_output([c.describe() for c in planned])
                except Exception as error:
                    step.fail(f"no plan: {error!r}")
            if planned:
                with trace.step("cut_planned", kind="code", input=[c.describe() for c in planned]) as step:
                    solid, _ = self.apply_cuts(solid, planned, view_set, target, step, deadline)

        if trace.current_case().dir is not None:
            with trace.step("views_after", kind="check") as step:
                for name, image in view_set.iso_overlay(solid).items():
                    step.attach(f"{name}_after.png", cv2.imencode(".png", image)[1].tobytes())
        return solid

    def apply_cuts(self, solid, cuts: list, view_set: ViewSet, target: float | None, step, deadline: float):
        """Make `cuts`, keeping only those that pass the checks (see `features`): all together first,
        then group by group, then one by one within a group that failed. Returns the solid and the kept cuts."""
        tools = {}
        for cut in cuts:
            try:
                tools[id(cut)] = features.tool(cut)
            except Exception as error:
                step.log(f"{cut.name}: not built ({error})", level="warning")
        cuts = [c for c in cuts if id(c) in tools]
        if not cuts:
            return solid, []
        missing = view_set.missing(solid)
        start_volume = vh.volume(solid)

        def attempt(batch: list) -> str | None:
            """Cut `batch` from the current solid and keep the result if it passes; else say why not."""
            nonlocal solid, missing
            if time.monotonic() > deadline:
                return "out of time"
            try:
                carved = self.cutter.subtract(solid, features.compound([tools[id(c)] for c in batch]),
                                              FEATURE_CUT_TIMEOUT)
            except Exception as error:
                return f"boolean failed: {error!r}"
            if carved is None:
                return "boolean gave no valid solid"
            after = vh.volume(carved)
            if target and after < MIN_VOLUME_SHARE * target:
                return f"would leave {after:.0f} mm3, below the part's {target:.0f} mm3"
            now = view_set.missing(carved)
            worse = {v: now[v] - missing[v] for v in now if now[v] - missing[v] > view_set.tolerance(v)}
            if worse:
                return "opens background where the render shows the part: " + ", ".join(
                    f"{v} +{n} px" for v, n in worse.items())
            solid, missing = carved, now
            return None

        kept = []
        if (reason := attempt(cuts)) is None:
            kept = cuts
        else:
            step.log(f"all {len(cuts)} together: {reason}; trying them group by group")
            groups: dict[str, list] = {}
            for cut in cuts:
                groups.setdefault(cut.group or cut.name, []).append(cut)
            for name, group in groups.items():
                if (reason := attempt(group)) is None:
                    kept += group
                    continue
                if len(group) == 1:
                    step.log(f"{group[0].name}: rejected, {reason}", level="warning")
                    continue
                step.log(f"group {name}: {reason}; trying its cuts one by one")
                for cut in group:
                    if (reason := attempt([cut])) is None:
                        kept.append(cut)
                    else:
                        step.log(f"{cut.name}: rejected, {reason}", level="warning")
        removed = start_volume - vh.volume(solid)
        step.log(f"kept {len(kept)} of {len(cuts)} cuts")
        step.set_output({"kept": [c.name for c in kept], "removed_mm3": round(removed, 2)})
        step.metric(kept=f"{len(kept)}/{len(cuts)}", removed_cm3=round(removed / 1000, 3),
                    **({"vs_true_volume": round(vh.volume(solid) / target, 3)} if target else {}))
        if kept and trace.current_case().dir is not None:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"stage_{step.name}.stp"
                vh.write_step(solid, path)
                step.attach_file(path, role="stage")
        return solid, kept
