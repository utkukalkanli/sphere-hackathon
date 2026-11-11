"""Deterministic first step: bounding box carved by the silhouettes of the views. No model is called."""

import os
import tempfile
from pathlib import Path

from agentic_cad import trace
from agentic_cad import visual_hull as vh

# A cut that leaves less than this share of the part's true volume removed real material: undo it
MIN_VOLUME_SHARE = 0.97
# Seconds one cut may take before it is abandoned and the previous solid kept. The whole run over all
# test cases has a 20 minute limit; the straight cuts normally take 1-3 s, the diagonal one 5-40 s.
CUT_TIMEOUT = float(os.environ.get("HULL_CUT_TIMEOUT", 30))
ISO_CUT_TIMEOUT = float(os.environ.get("HULL_ISO_CUT_TIMEOUT", 60))


class VisualHullAgent:
    def __init__(self, use_iso: bool = True):
        self.use_iso = use_iso
        self.cutter = vh.Cutter()

    def close(self) -> None:
        self.cutter.close()

    def generate_cad(self, prompt: str, views: list[Path], context: Path | None, output_path: Path) -> None:
        solid = self.build(prompt, views)
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
