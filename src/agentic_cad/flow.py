"""Flow engine: a LangGraph with a fixed order of nodes, built to work from the brief and the views alone.

    read_brief   envelope, volume and through-hole count from the brief (regex); the holes the brief locates
                 as data (one model call with a JSON schema). facts.json is used instead when the case has one.
    base         envelope block, then the silhouette hull (pipeline.build_hull), no model call
    holes        every located hole cut into the hull, guarded one by one; whether a hole goes through is
                 read off the reference views (hull.see_through), not taken from the wording of the brief
    check        envelope, volume, silhouette match and through-hole count of the best solid against the brief
    refine       the model proposes further cuts as a feature list (pipeline.carve), only while check fails
    finish

The model never writes code and never decides what happens next: it fills in two JSON schemas. Every solid goes
through the same runner and evaluation as in the hull engine, and the output file is only ever replaced by a
solid that verifies better.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agentic_cad import features, hull, prompts
from agentic_cad.agent import fallback_script, load_facts
from agentic_cad.config import Settings
from agentic_cad.evaluate import TOL_BBOX, TOL_VOLUME
from agentic_cad.llm import ChatClient, LLMError
from agentic_cad.pipeline import OUTLINE_SLACK, Case, _better, _holes_off, _usable, build_hull, carve
from agentic_cad.trace import CaseTrace

BRIEF_MARGIN_S = 60       # the hole extraction is skipped with less time than this left
OPEN_SHARE = 0.97         # share of a hole's disc (95 % of its diameter) that must be background for a through cut
FACE_EPS = 0.05           # mm: a hole end this close to an envelope face starts outside the part


class FlowState(TypedDict, total=False):
    holes: list[dict]      # {label, diameter, axis, centre, length, through}
    stage: str
    cut: list[str]         # labels of the holes that were cut into the base
    ok: bool               # the best solid passes every check
    findings: list[str]
    refined: bool
    stop: str | None


# -- the holes of the brief -------------------------------------------------------------------------

def holes_from_facts(facts: dict) -> list[dict]:
    """The hole inventory of facts.json in the form of the brief extraction (through: unknown)."""
    holes = []
    for item in facts.get("cylindrical_holes_and_bores") or []:
        try:
            diameter = float(item["diameter"])
            lengths = [float(v) for v in item.get("lengths") or []]
            centres = [[float(v) for v in centre] for centre in item.get("centres") or []]
        except (KeyError, TypeError, ValueError):
            continue
        axis = hull._axis_vector(item.get("axis", ""))
        if axis is None or diameter <= 0 or not lengths:
            continue
        for index, centre in enumerate(centres):
            if len(centre) != 3:
                continue
            length = lengths[index] if len(lengths) == len(centres) else max(lengths)
            holes.append({"label": f"d={diameter:g} axis {item.get('axis')}", "diameter": diameter, "axis": axis,
                          "centre": centre, "length": length, "through": None})
    return holes


def parse_holes(reply: str) -> list[dict]:
    """The model's reply (prompts.HOLES_SCHEMA) as one record per hole; anything unusable is dropped."""
    try:
        data = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
    except ValueError:
        return []
    holes = []
    for item in data.get("holes") or [] if isinstance(data, dict) else []:
        try:
            diameter, length = float(item["diameter"]), float(item["length"])
            axis = str(item["axis"]).lower()
            centres = [[float(v) for v in centre] for centre in item["centres"]]
        except (KeyError, TypeError, ValueError):
            continue
        if axis not in ("x", "y", "z") or diameter <= 0 or length <= 0:
            continue        # inclined holes: the brief rarely gives enough to place them
        for centre in centres:
            if len(centre) == 3:
                holes.append({"label": " ".join(str(item.get("label") or "hole").split())[:60], "diameter": diameter,
                              "axis": axis, "centre": centre, "length": length, "through": bool(item.get("through"))})
    return holes


def hole_features(holes: list[dict], views: dict[str, Path], box: dict) -> list[dict]:
    """The holes as cylinder features (features.py). A hole along a principal axis is through when the views
    show daylight over its whole disc, whatever the brief says; one the views show closed is cut by
    cadlib.cut_closed (blind from both ends if it would otherwise be a straight passage)."""
    out = []
    for hole in holes:
        axis, d, centre, length = hole["axis"], hole["diameter"], hole["centre"], hole["length"]
        label = f"{hole['label']} d={d:g} at ({centre[0]:g}, {centre[1]:g}, {centre[2]:g})"
        if not isinstance(axis, str):       # inclined (facts.json only): as located, the brief's wording decides
            start = [c - 0.5 * length * a for c, a in zip(centre, axis)]
            end = [c + 0.5 * length * a for c, a in zip(centre, axis)]
            out.append({"type": "cylinder", "label": label, "diameter": d, "start": start, "end": end,
                        "through": bool(hole["through"])})
            continue
        k = "xyz".index(axis)
        extent = float(box[axis])
        lo, hi = centre[k] - length / 2, centre[k] + length / 2
        if lo <= FACE_EPS:
            lo = min(lo, 0.0) - 1.0
        if hi >= extent - FACE_EPS:
            hi = max(hi, extent) + 1.0

        def segment(a: float, b: float, through: bool, name: str = label) -> dict:
            start, end = list(centre), list(centre)
            start[k], end[k] = a, b
            return {"type": "cylinder", "label": name, "diameter": d, "start": start, "end": end, "through": through}

        seen = hull.see_through(views, box, axis, centre, d)
        if seen is None:
            out.append(segment(lo, hi, bool(hole["through"])))
        elif not seen:
            out.append({**segment(lo, hi, False), "closed": True})
        elif (hull.daylight(views, box, axis, centre, d, 0.95) or 0.0) >= OPEN_SHARE:
            out.append(segment(lo, hi, True))
        # else: daylight in the middle but not over the whole disc: the opening is not this cylinder (e.g. two
        # overlapping bores described as one). The hull already has its true outline from the views.
    return out


# -- the graph ----------------------------------------------------------------------------------------

def build_graph(case: Case, description: str, views: list[Path]):
    s = case.s
    by_name = {prompts.view_name(path): path for path in views}
    wanted = case.facts.get("through_holes_genus")
    wanted = int(wanted) if isinstance(wanted, (int, float)) else None

    def read_brief(state: FlowState) -> dict:
        holes = holes_from_facts(case.facts)
        if holes:
            case.trace.log(f"{len(holes)} holes from facts.json")
            return {"holes": holes}
        if not s.llm_stage or case.remaining() < BRIEF_MARGIN_S:
            return {"holes": []}
        message = prompts.holes_message(description, case.facts)
        with case.trace.step("holes of the brief", kind="llm", input=message) as step:
            try:
                reply, usage = ChatClient(s).chat(
                    [{"role": "system", "content": prompts.HOLES_SYSTEM_PROMPT}, {"role": "user", "content": message}],
                    timeout=min(s.llm_timeout_s, case.remaining() - 30), temperature=0.0,
                    extra={"chat_template_kwargs": {"enable_thinking": s.enable_thinking},
                           "response_format": prompts.HOLES_RESPONSE_FORMAT})
            except LLMError as exc:
                step.log(f"LLM call failed: {exc}", "error")
                return {"holes": []}
            for key in case.usage:
                case.usage[key] += int(usage.get(key) or 0)
            holes = parse_holes(reply)
            step.done(output=reply, metrics={"holes": len(holes), **{k: usage.get(k) for k in case.usage}})
        return {"holes": holes}

    def base(state: FlowState) -> dict:
        block = case.build("fallback block", fallback_script(case.facts), timeout=60.0)
        if _usable(block):
            case.keep(block)
        stage = "block"
        solid = build_hull(case, by_name)
        if solid is not None and (case.best is None or solid.evaluation.rank >= case.best.evaluation.rank):
            case.keep(solid)
            stage = "hull"
        return {"stage": stage}

    def holes(state: FlowState) -> dict:
        start = case.best
        box = case.facts.get("bounding_box_mm") or {}
        if start is None or state.get("stage") != "hull" or not state.get("holes") or not all(k in box for k in "xyz"):
            return {"cut": []}
        wanted_cuts = hole_features(state["holes"], by_name, box)
        floor = 0.97 * float(case.facts.get("volume_mm3") or 0.0)
        attempt = case.build("holes of the brief", features.compile_script(wanted_cuts, floor, wanted),
                             base=start.step_path)
        log = attempt.report.get("feature_log") or []
        for line in log:
            case.trace.log(line)
        if not _better(attempt, start, start.evaluation.rank - OUTLINE_SLACK, wanted):
            case.trace.log("holes of the brief rejected: the part broke, lost its outline or got no closer", "warn")
            return {"cut": []}
        case.keep(attempt)
        return {"cut": [line.split(" -> ")[0] for line in log if "-> cut" in line], "stage": "hull+holes"}

    def check(state: FlowState) -> dict:
        best = case.best
        if best is None:
            return {"ok": False, "findings": ["no solid was built"]}
        ev = best.evaluation
        findings = []
        if ev.bbox_err is not None and ev.bbox_err > TOL_BBOX:
            findings.append(f"envelope off by {100 * ev.bbox_err:.1f}%")
        if ev.volume_err is not None and abs(ev.volume_err) > TOL_VOLUME:
            findings.append(f"volume {100 * ev.volume_err:+.1f}% against the brief")
        off = _holes_off(best, wanted)
        if off:
            findings.append(f"{best.report.get('through_holes')} through-holes, the brief says {wanted}")
        if ev.iou_min is not None and ev.iou_min < 0.92:
            findings.append(f"worst silhouette match {ev.iou_min:.2f}")
        case.trace.log("check: " + ("; ".join(findings) if findings else "every check passes"))
        return {"ok": not findings, "findings": findings}

    def refine(state: FlowState) -> dict:
        before = case.best
        stop = carve(case, before, state.get("cut") or [], description, views)
        stage = state.get("stage", "block") + ("+refined" if case.best is not before else "")
        return {"refined": True, "stop": stop, "stage": stage}

    def after_check(state: FlowState) -> str:
        if state.get("ok") or state.get("refined") or not s.llm_stage or case.best is None:
            return END
        return "refine"

    graph = StateGraph(FlowState)
    for name, node in (("read_brief", read_brief), ("base", base), ("holes", holes), ("check", check),
                       ("refine", refine)):
        graph.add_node(name, node)
    graph.add_edge(START, "read_brief")
    graph.add_edge("read_brief", "base")
    graph.add_edge("base", "holes")
    graph.add_edge("holes", "check")
    graph.add_conditional_edges("check", after_check, ["refine", END])
    graph.add_edge("refine", "check")
    return graph.compile()


# -- entry point ------------------------------------------------------------------------------------

def run_case(settings: Settings, name: str, case_dir: Path, description: str, views: list[Path],
             context: Path | None, output_path: Path, deadline: float, trace: CaseTrace) -> dict:
    """Same contract as CadAgent.generate_cad: always writes output_path when anything valid exists."""
    work = trace.dir if trace.enabled else Path(tempfile.mkdtemp(prefix=f"{name}_"))
    facts = load_facts(case_dir, description, settings.use_facts)
    case = Case(settings, case_dir, facts, output_path, deadline, trace, work)
    trace.start()
    status, error, final = "ok", None, {}
    try:
        final = build_graph(case, description, views).invoke({"refined": False, "stop": None})
        trace.log(f"finished at stage {final.get('stage')}"
                  + (f", loop stopped: {final.get('stop')}" if final.get("stop") else "")
                  + ("" if final.get("ok") else f"; open: {'; '.join(final.get('findings') or [])}"))
    except Exception as exc:   # never let one case take the run down
        status, error = "error", f"{type(exc).__name__}: {exc}"
        trace.log(error, "error")
    finally:
        if case.best is not None and case.best.step_path is not None:
            trace.artifact(case.best.step_path, role="output")
        trace.end(status if output_path.is_file() else "error", error)
        if not trace.enabled:
            shutil.rmtree(work, ignore_errors=True)
    summary = {"name": name, "status": status, "error": error, "written": output_path.is_file(),
               "attempt": case.best.number if case.best else None, "builds": case.count,
               "stage": final.get("stage"), "stop": final.get("stop"), "checks_pass": bool(final.get("ok")),
               "through_holes": case.best.report.get("through_holes") if case.best else None, **case.usage}
    if case.best is not None:
        summary.update(case.best.evaluation.metrics())
    return summary
