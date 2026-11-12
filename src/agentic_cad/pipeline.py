"""Hull engine: deterministic stages first, then a fixed generate -> verify -> keep-the-best loop.

    0. envelope block          a valid file exists before anything else happens
    1. silhouette hull         front/right/top outlines extruded and intersected (hull.py), no model call
    2. bores from the facts    every located hole/bore cut into the hull, kept only if the part stays whole
    3. carving rounds          the model gets the solid of stage 2 as `base` and only removes hidden material
                               (pockets, blind holes, cavities); several candidates per round run in parallel,
                               the best verified one is kept and fed back

Every stage goes through the same runner and evaluation as any attempt, and the output file is replaced only
by a solid that verifies better, so a later stage can never make the result worse than an earlier one.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentic_cad import features, hull, prompts
from agentic_cad.agent import Attempt, fallback_script, load_facts, make_attempt, publish
from agentic_cad.config import Settings
from agentic_cad.evaluate import TOL_BBOX
from agentic_cad.llm import ChatClient, LLMError
from agentic_cad.trace import CaseTrace

STOP_MARGIN_S = 40          # do not start a round with less time than this left
OUTLINE_SLACK = 0.01        # a carved solid may lose this much silhouette match against its base
MIN_GAIN = 0.002            # volume error must shrink by at least this for a candidate to replace the best
_builds = threading.BoundedSemaphore(max(2, (os.cpu_count() or 4) - 1))   # CAD kernels running at once


def _usable(attempt: Attempt | None) -> bool:
    return bool(attempt and attempt.report.get("ok") and attempt.report.get("valid", True)
                and attempt.report.get("solids_in_result", 1) == 1 and attempt.step_path is not None)


def _normalise(code: str) -> str:
    return "\n".join(line.split("#", 1)[0].rstrip() for line in code.splitlines() if line.split("#", 1)[0].strip())


class Case:
    def __init__(self, settings: Settings, case_dir: Path, facts: dict, output_path: Path, deadline: float,
                 trace: CaseTrace, work: Path):
        self.s, self.case_dir, self.facts, self.output_path = settings, case_dir, facts, output_path
        self.deadline, self.trace, self.work = deadline, trace, work
        self.best: Attempt | None = None
        self.count = 0
        self.lock = threading.Lock()
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def remaining(self) -> float:
        return self.deadline - time.time()

    def build(self, name: str, code: str, base: Path | None = None, timeout: float | None = None) -> Attempt:
        with self.lock:
            number = self.count
            self.count += 1
        with _builds, self.trace.step(f"{name} (attempt {number})", kind="code") as step:
            limit = max(10.0, min(timeout or self.s.exec_timeout_s, self.remaining() - 5))
            return make_attempt(number, code, self.work, self.case_dir, self.facts, self.s, limit, step, base)

    def keep(self, attempt: Attempt) -> None:
        self.best = attempt
        publish(attempt, self.output_path, self.trace)


# -- stages 1 and 2 ---------------------------------------------------------------------------------

def build_hull(case: Case, views: dict[str, Path]) -> Attempt | None:
    """The best silhouette hull that builds: all three views if possible, else fewer."""
    box = case.facts.get("bounding_box_mm") or {}
    if not all(k in box for k in "xyz"):
        return None
    for tolerance in (0.75, 2.0):
        try:
            outlines = hull.view_outlines(views, box, tolerance)
        except Exception as exc:
            case.trace.log(f"outline extraction failed: {type(exc).__name__}: {exc}", "warn")
            return None
        candidates = hull.hull_candidates(outlines, box)
        full = [c for c in candidates if c[0].count("+") == 2]
        if full:
            attempt = case.build(f"hull {full[0][0]}", full[0][1])
            if _usable(attempt):
                return attempt
    # the three-way intersection failed: fall back to pairs, then to single extrusions
    for size in (1, 0):
        built = [case.build(f"hull {label}", script) for label, script in candidates if label.count("+") == size]
        built = [a for a in built if _usable(a)]
        if built:
            return max(built, key=lambda a: a.evaluation.rank)
    return None


def cut_bores(case: Case, base: Attempt) -> tuple[Attempt, list[str]]:
    """Cut the holes/bores the facts sheet locates into `base`. Each cut is skipped inside the script if it
    would split the part or take it below the target volume; the whole stage is dropped if the outline suffers."""
    cuts = hull.bore_cuts(case.facts)
    if not cuts:
        return base, []
    floor = 0.97 * float(case.facts.get("volume_mm3") or 0.0)
    lines = ["# holes and bores located by the facts sheet, cut into the silhouette hull", "body = base"]
    holes = case.facts.get("through_holes_genus")   # a bore that opens more through-holes than the part has is wrong
    limit = f", {int(holes)}" if isinstance(holes, (int, float)) else ""
    lines += [f"body = try_cut(body, {expr}, {floor:.1f}{limit})   # {label}" for label, expr in cuts]
    lines.append("result = body")
    attempt = case.build("facts bores", "\n".join(lines) + "\n", base=base.step_path)
    if not _usable(attempt) or attempt.evaluation.rank < base.evaluation.rank - OUTLINE_SLACK:
        case.trace.log("facts bores rejected: the part broke or its outline got worse", "warn")
        return base, []
    b, a = base.evaluation.volume_err, attempt.evaluation.volume_err
    if b is not None and a is not None and abs(a) >= abs(b):
        return base, []
    return attempt, [label for label, _ in cuts]


# -- stage 3 ----------------------------------------------------------------------------------------

def _better(candidate: Attempt, best: Attempt, floor_rank: float) -> bool:
    """A carved solid replaces the best one when it is a valid single solid with the right envelope, keeps
    the outline, and is closer to the target volume."""
    ev, ref = candidate.evaluation, best.evaluation
    if not _usable(candidate) or ev.rank < floor_rank:
        return False
    if ev.bbox_err is not None and ev.bbox_err > TOL_BBOX:
        return False
    if ev.volume_err is None or ref.volume_err is None:
        return ev.rank > ref.rank
    return abs(ev.volume_err) < abs(ref.volume_err) - MIN_GAIN


def carve(case: Case, base: Attempt, bores: list[str], description: str, views: list[Path] | None = None) -> str:
    """Rounds of parallel candidates on top of `base`. Returns why the loop stopped.

    In "script" mode a candidate is a CadQuery script written by the model; in "features" mode it is a feature
    list in the JSON schema of features.py, compiled into a script here. Both are built and judged the same way."""
    s = case.s
    by_features = s.carve_mode == "features"
    floor_rank = base.evaluation.rank - OUTLINE_SLACK
    extra: dict = {"chat_template_kwargs": {"enable_thinking": s.enable_thinking}}
    if by_features:
        extra["response_format"] = features.RESPONSE_FORMAT
    min_volume = 0.97 * float(case.facts.get("volume_mm3") or 0.0)
    holes = case.facts.get("through_holes_genus")
    max_holes = int(holes) if isinstance(holes, (int, float)) else None
    images = prompts.view_parts(views, s.prompt_image_px) if by_features and s.carve_images and views else []
    lists: dict[int, list[dict]] = {}     # attempt number -> the feature list it was compiled from
    clients = [ChatClient(s) for _ in range(max(1, s.candidates))]
    best_code: str | None = None
    seen: set[str] = set()
    failures: list[str] = []
    idle = 0
    for round_no in range(1, s.max_steps + 1):
        if case.best.evaluation.accepted:
            return "accepted"
        if case.remaining() < STOP_MARGIN_S:
            return "time"
        if idle >= s.patience:
            return "no improvement"
        best_lines = case.best.evaluation.lines + (case.best.report.get("feature_log") or []) if best_code else []
        build_message = prompts.feature_message if by_features else prompts.carve_message
        message = build_message(description, case.facts, base.evaluation.lines, bores, best_code, best_lines, failures)
        system = prompts.FEATURE_SYSTEM_PROMPT if by_features else prompts.CARVE_SYSTEM_PROMPT
        content = [*images, {"type": "text", "text": message}] if images else message
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]

        def one(index: int) -> Attempt | None:
            # spread the temperature so the candidates of a round differ; later rounds explore more
            temperature = min(1.0, s.temperature + 0.25 * index + 0.1 * idle)
            with case.trace.step(f"round {round_no} candidate {index + 1}", kind="llm",
                                 input=message if index == 0 else f"(same prompt, temperature {temperature:.2f})") as step:
                try:
                    reply, usage = clients[index].chat(messages, timeout=min(s.llm_timeout_s, case.remaining() - 20),
                                                       temperature=temperature, extra=extra)
                except LLMError as exc:
                    step.log(f"LLM call failed: {exc}", "error")
                    return None
                with case.lock:
                    for key in case.usage:
                        case.usage[key] += int(usage.get(key) or 0)
                step.done(output=reply, metrics={k: usage.get(k) for k in case.usage})
            wanted = features.parse(reply) if by_features else None
            if by_features:
                code = features.compile_script(wanted, min_volume, max_holes) if wanted else None
            else:
                code = prompts.extract_code(reply)
            if code is None:
                return None
            key = _normalise(code)
            with case.lock:
                if key in seen:
                    return None
                seen.add(key)
            if case.remaining() < 15:
                return None
            attempt = case.build(f"round {round_no} candidate {index + 1}", code, base=base.step_path)
            if wanted:
                with case.lock:
                    lists[attempt.number] = wanted
            return attempt

        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            attempts = [a for a in pool.map(one, range(len(clients))) if a is not None]

        improved = False
        for attempt in sorted(attempts, key=lambda a: abs(a.evaluation.volume_err or 9.0)):
            if _better(attempt, case.best, floor_rank):
                case.keep(attempt)
                best_code = features.dumps(lists[attempt.number]) if attempt.number in lists else attempt.code
                improved = True
                break
        for attempt in attempts:
            if attempt is case.best:
                continue
            ev = attempt.evaluation
            if not attempt.report.get("ok"):
                failures.append(f"failed: {str(attempt.report.get('error'))[:160]} (use the helpers exactly as "
                                "documented)")
            elif not _usable(attempt) or (ev.bbox_err or 0) > TOL_BBOX:
                failures.append("a cut split the part into pieces or changed its bounding box: "
                                + "; ".join(attempt.report.get("notes") or ["cut less deep / smaller"])[:200])
            elif ev.rank < floor_rank:
                failures.append(f"a cut removed material that the outlines show (silhouette match fell to {ev.rank:.3f}): "
                                "stay inside the part")
            elif ev.volume_err is not None:
                skipped = [line for line in attempt.report.get("feature_log") or [] if "-> cut" not in line]
                failures.append(f"no better than the best {'list' if by_features else 'script'} (volume "
                                f"{100 * ev.volume_err:+.1f}% vs target)" + ("; " + "; ".join(skipped[:3]) if skipped else ""))
        if not attempts:
            failures.append("the reply held no new feature list" if by_features
                            else "the reply contained no new script in a ```python block")
        idle = 0 if improved else idle + 1
        case.trace.log(f"round {round_no}: {len(attempts)} built, " + ("improved" if improved else "no improvement")
                       + (f", volume {100 * case.best.evaluation.volume_err:+.1f}%"
                          if case.best.evaluation.volume_err is not None else ""))
    return "rounds"


# -- entry point ------------------------------------------------------------------------------------

def run_case(settings: Settings, name: str, case_dir: Path, description: str, views: list[Path],
             context: Path | None, output_path: Path, deadline: float, trace: CaseTrace) -> dict:
    """Same contract as CadAgent.generate_cad: always writes output_path when anything valid exists."""
    work = trace.dir if trace.enabled else Path(tempfile.mkdtemp(prefix=f"{name}_"))
    case = Case(settings, case_dir, load_facts(case_dir, description), output_path, deadline, trace, work)
    by_name = {prompts.view_name(path): path for path in views}
    trace.start()
    status, error, stop, stage = "ok", None, None, "block"
    try:
        block = case.build("fallback block", fallback_script(case.facts), timeout=60.0)
        if _usable(block):
            case.keep(block)
        base = build_hull(case, by_name)
        if base is not None and (case.best is None or base.evaluation.rank >= case.best.evaluation.rank):
            case.keep(base)
            stage = "hull"
        base = case.best
        bores: list[str] = []
        if base is not None and stage == "hull":
            with_bores, bores = cut_bores(case, base)
            if with_bores is not base:
                case.keep(with_bores)
                base, stage = with_bores, "hull+bores"
        # Carving only pays off on parts the outlines say little about. On 60 eval parts with ground truth it
        # raised the volume IoU where the hull was more than twice too heavy (hollow parts) and lowered it
        # elsewhere: the model reaches the target volume by removing material in the wrong place.
        excess = base.evaluation.volume_err if base is not None else None
        if base is not None and settings.llm_stage and excess is not None and excess < settings.carve_min_excess:
            trace.log(f"no carving: the base is only {100 * excess:+.0f}% off the target volume")
        elif base is not None and settings.llm_stage:
            before = case.best
            stop = carve(case, base, bores, description, views)
            if case.best is not before:
                stage += "+carved"
        trace.log(f"finished at stage {stage}" + (f", loop stopped: {stop}" if stop else ""))
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
               "attempt": case.best.number if case.best else None, "builds": case.count, "stage": stage,
               "stop": stop, **case.usage}
    if case.best is not None:
        summary.update(case.best.evaluation.metrics())
    return summary
