"""Deterministic: a part that is a thin shell, hollowed out.

Some parts are a skin around a space no view looks into: a turbocharger volute whose channel
opens only through a slot and a pipe (practice part 19). Their silhouettes give the outside, and
every step after that works on what the views show; the inside stays filled, with several times
the part's volume.

The views say nothing about it, but facts.json does. A shell has an inside and an outside, so
its surface is about twice the outside's, while its volume is the outside's surface times the
wall. If our solid is the outside of such a part, filled, it therefore has

- several times the true volume, and
- about half the true surface.

Both together are the sign. A solid part with open recesses still to cut has too much volume and
roughly the right surface or more; no other of the 44 solids this was looked at on has the two
(24 eval parts, 20 practice parts; only practice 19: 6.35 times the volume, 0.54 of the surface).

Then the solid is hollowed: what is left of it after moving it by the wall in the six axis
directions lies at least a wall inside the surface, and is taken out. The wall starts at true
volume / our surface and is corrected until the shell has the part's volume: the wall is solved
from the volume, nothing in the views gives it. The result is kept only if that succeeds, its
surface then comes out near the true one, and the verifier does not rate it lower.

The space inside is closed where the part has an opening no step found; that costs the skin over
the opening, not the shell.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.gp import gp_Trsf, gp_Vec
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import trace, verify
from agentic_cad import visual_hull as vh

ENABLED = os.environ.get("HULL_SHELL", "1") != "0"
MIN_VOLUME = 2.0             # our solid has at least this many times the true volume
SURFACE = (0.4, 0.65)        # ... and this share of the true surface: about half
MAX_WALL = 0.1               # the wall is at most this share of the box's smallest side
VOLUME_FITS = (0.97, 1.10)   # the shell's volume over the true one when the wall is right
SURFACE_FITS = (0.75, 1.35)  # ... and its surface over the true one
ROUNDS = 4                   # corrections of the wall
TIMEOUT = float(os.environ.get("HULL_SHELL_TIMEOUT", 60))
SCORE_SLACK = 0.003          # the verifier's score may drop by this much and the shell still pass
AXES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def hollow(shape: TopoDS_Shape, wall: float) -> TopoDS_Shape | None:
    """`shape` without its inside: what stays in it when it is moved by `wall` along each axis,
    both ways, is at least that far inside its surface, and is taken out. Runs in a child process."""
    inner = shape
    for axis in AXES:
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(*(wall * float(value) for value in axis)))
        common = BRepAlgoAPI_Common(inner, BRepBuilderAPI_Transform(shape, move, True).Shape())
        if not common.IsDone():
            return None
        inner = common.Shape()
    if vh.volume(inner) <= 0:
        return None
    cut = BRepAlgoAPI_Cut(shape, inner)
    if not cut.IsDone():
        return None
    result = vh.largest_solid(cut.Shape())
    if result is None or not BRepCheck_Analyzer(result).IsValid():
        return None
    return result


def sign(solid: TopoDS_Shape, case_dir: Path) -> tuple[float, float, float] | None:
    """Our volume and surface over the true ones and the wall to start from, or None without facts."""
    try:
        facts = json.loads((case_dir / "facts.json").read_text())
        volume, surface = float(facts["volume_mm3"]), float(facts["surface_area_mm2"])
    except Exception:
        return None
    if volume <= 0 or surface <= 0:
        return None
    ours, skin = verify.measure(solid)
    return ours / volume, skin / surface, volume / skin


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step: hollow the solid if volume and surface say the part is a shell."""
    with trace.step("hollow_shell", kind="code") as step:
        if case_dir is None or not ENABLED:
            return solid
        found = sign(solid, case_dir)
        if found is None:
            step.log("facts.json gives no volume and surface to compare with")
            return solid
        volume, surface, wall = found
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        step.metric(volume_over_true=round(volume, 3), surface_over_true=round(surface, 3), wall_mm=round(wall, 3))
        if volume < MIN_VOLUME or not SURFACE[0] <= surface <= SURFACE[1] or wall > MAX_WALL * min(dims.values()):
            step.log(f"{volume:.2f} times the true volume with {surface:.2f} of the true surface: not the sign of a filled shell "
                     f"(at least {MIN_VOLUME:g} times and {SURFACE[0]:g} to {SURFACE[1]:g})")
            return solid
        target = vh.read_volume(case_dir)
        true_surface = verify.measure(solid)[1] / surface
        shell = None
        for _ in range(ROUNDS):
            if agent.out_of_time():
                step.log("out of time for this case; the solid stays filled", level="warning")
                return solid
            try:
                candidate = agent.cutter.run(hollow, solid, wall, TIMEOUT)
            except Exception as error:
                step.log(f"hollowing with a wall of {wall:.2f} mm raised {error!r}", level="warning")
                return solid
            if candidate is None:
                step.log(f"hollowing with a wall of {wall:.2f} mm gave no valid solid", level="warning")
                return solid
            left = vh.volume(candidate) / target
            step.log(f"a wall of {wall:.2f} mm leaves {left:.3f} of the true volume")
            if VOLUME_FITS[0] <= left <= VOLUME_FITS[1]:
                shell = candidate
                break
            wall *= float(np.clip(1.0 / left, 0.5, 2.0))   # a thin shell's volume goes with its wall
        if shell is None:
            step.log("no wall gives the part's volume; the solid stays filled", level="warning")
            return solid
        skin = verify.measure(shell)[1] / true_surface
        if not SURFACE_FITS[0] <= skin <= SURFACE_FITS[1]:
            step.log(f"the shell has {skin:.2f} of the true surface: not this part's shape; the solid stays filled", level="warning")
            return solid
        observed = agent.evidence(case_dir, dims).observed
        before, after = verify.check(solid, case_dir, observed).score, verify.check(shell, case_dir, observed).score
        if after < before - SCORE_SLACK:
            step.log(f"the verifier rates the shell lower ({before:.3f} to {after:.3f}); the solid stays filled", level="warning")
            return solid
        step.metric(wall_mm=round(wall, 3), shell_surface_over_true=round(skin, 3),
                    volume_cm3=round(vh.volume(shell) / 1000, 3), vs_true_volume=round(vh.volume(shell) / target, 3))
        step.log(f"hollowed with a wall of {wall:.2f} mm: {skin:.2f} of the true surface, verifier {before:.3f} to {after:.3f}")
        step._status = None
        agent._record(step, shell, "shell")
        return shell
