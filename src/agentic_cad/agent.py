"""The generate -> execute -> measure -> compare -> correct loop for one test case."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from agentic_cad import prompts
from agentic_cad.config import Settings
from agentic_cad.evaluate import Evaluation, evaluate, facts_from_description
from agentic_cad.llm import ChatClient, LLMError
from agentic_cad.trace import CaseTrace

SRC_DIR = Path(__file__).resolve().parents[1]


@dataclass
class Attempt:
    number: int
    code: str
    report: dict
    evaluation: Evaluation
    step_path: Path | None


def run_script(script: Path, out_dir: Path, stem: str, case_dir: Path | None, settings: Settings,
               timeout: float, base: Path | None = None, bbox: list[float] | None = None) -> dict:
    """Execute a generated script in a separate process (agentic_cad.runner) and read its report."""
    command = [sys.executable, "-m", "agentic_cad.runner", str(script), str(out_dir), "--stem", stem,
               "--image-size", str(settings.mask_px), "--tile", str(settings.tile_px if settings.feedback_images else 0)]
    if case_dir is not None:
        command += ["--case-dir", str(case_dir)]
    if base is not None:
        command += ["--base", str(base)]
    if bbox:
        command += ["--bbox", ",".join(f"{v:g}" for v in bbox)]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC_DIR), os.environ.get("PYTHONPATH")]))}
    report_path = out_dir / f"{stem}.json"
    report_path.unlink(missing_ok=True)
    try:
        proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"the script did not finish within {timeout:.0f} s. Simplify it: fewer boolean "
                                      "operations, no fillets on complex bodies, no huge loops.", "notes": []}
    if report_path.is_file():
        try:
            return json.loads(report_path.read_text())
        except json.JSONDecodeError:
            pass
    tail = (proc.stderr or proc.stdout or "").strip()[-600:]
    return {"ok": False, "error": f"the CAD kernel crashed while building the part (exit code {proc.returncode}). "
                                  f"This usually comes from an invalid boolean or fillet. {tail}", "notes": []}


def fallback_script(facts: dict) -> str:
    """A plain block of the envelope: scores something where a missing file scores zero."""
    box = facts.get("bounding_box_mm") or {}
    x, y, z = (float(box.get(k) or 10.0) for k in "xyz")
    return f"result = box(0, 0, 0, {x}, {y}, {z})\n"


class CadAgent:
    def __init__(self, settings: Settings, client: ChatClient | None = None):
        self.settings = settings
        self.client = client or ChatClient(settings)

    def generate_cad(self, name: str, case_dir: Path, description: str, views: list[Path], context: Path | None,
                     output_path: Path, deadline: float, trace: CaseTrace) -> dict:
        """Writes output_path (always, as soon as anything valid exists) and returns a summary."""
        s = self.settings
        if s.engine == "hull":
            from agentic_cad.pipeline import run_case
            return run_case(s, name, case_dir, description, views, context, output_path, deadline, trace)
        if s.engine == "flow":
            from agentic_cad.flow import run_case
            return run_case(s, name, case_dir, description, views, context, output_path, deadline, trace)
        if s.engine == "graph":
            from agentic_cad.graph import run_case
            return run_case(s, name, case_dir, description, views, context, output_path, deadline, trace)
        work = trace.dir if trace.enabled else Path(tempfile.mkdtemp(prefix=f"{name}_"))
        facts = load_facts(case_dir, description)
        trace.start()
        best: Attempt | None = None
        status, error = "ok", None
        try:
            # 0. guarantee an output file before any model call
            with trace.step("fallback block", kind="code") as step:
                best = self._attempt(0, fallback_script(facts), work, case_dir, facts, min(60.0, s.exec_timeout_s), step)
                self._publish(best, output_path, trace, step)

            messages = [{"role": "system", "content": prompts.SYSTEM_PROMPT},
                        {"role": "user", "content": prompts.initial_message(description, facts, views, context,
                                                                            s.use_images, s.prompt_image_px)}]
            turns: list[list[dict]] = []           # [assistant, user feedback] pairs
            best_llm: Attempt | None = None
            for number in range(1, s.max_iters + 1):
                remaining = deadline - time.time()
                if remaining < 45:
                    trace.log(f"stopping before attempt {number}: {remaining:.0f} s left", "warn")
                    break
                with trace.step(f"attempt {number}", kind="tool") as outer:
                    # keep the context small: the brief plus the two most recent exchanges
                    window = messages + [m for pair in turns[-2:] for m in pair]
                    with trace.step("generate script", kind="llm",
                                    input=prompts.describe_for_trace(window[-1]["content"])) as step:
                        llm_timeout = min(s.llm_timeout_s, remaining - 30)
                        temperature = s.temperature if number <= 2 else min(0.7, s.temperature + 0.15 * (number - 2))
                        reply, usage = self.client.chat(window, timeout=llm_timeout, temperature=temperature)
                        step.done(output=reply, metrics={k: usage[k] for k in ("prompt_tokens", "completion_tokens")
                                                         if k in usage})
                    code = prompts.extract_code(reply)
                    if code is None:
                        feedback = ["Your reply contained no ```python block with a script that assigns `result`."]
                        attempt = None
                        outer.log("no code block in the reply", "warn")
                    else:
                        with trace.step("build, measure, compare", kind="code") as step:
                            timeout = max(10.0, min(s.exec_timeout_s, deadline - time.time() - 10))
                            attempt = self._attempt(number, code, work, case_dir, facts, timeout, step)
                        feedback = attempt.evaluation.lines
                        if attempt.report.get("ok") and (best_llm is None or
                                                         attempt.evaluation.rank > best_llm.evaluation.rank):
                            best_llm = attempt
                            if best is None or attempt.evaluation.rank >= best.evaluation.rank:
                                best = attempt
                                self._publish(best, output_path, trace, outer)
                        outer.done(metrics=attempt.evaluation.metrics())
                        if attempt.evaluation.accepted:
                            outer.log("accepted: within tolerance on every check")
                            break
                    best_note = None
                    if best_llm is not None and attempt is not None and attempt is not best_llm:
                        best_note = (f"This is worse than attempt {best_llm.number} (silhouette match "
                                     f"{best_llm.evaluation.rank:.3f} vs {attempt.evaluation.rank:.3f}). Go back to "
                                     f"that version and improve it instead:\n```python\n{best_llm.code}```")
                    sheets = [work / p for p in (attempt.report.get("sheets") or [])] if attempt else []
                    turns.append([
                        {"role": "assistant", "content": reply},
                        {"role": "user", "content": prompts.feedback_message(
                            number, feedback, sheets, s.max_iters - number, best_note,
                            s.use_images and s.feedback_images)},
                    ])
        except LLMError as exc:
            status, error = "error", f"LLM endpoint: {exc}"
            trace.log(error, "error")
        except Exception as exc:   # never let one case take the run down
            status, error = "error", f"{type(exc).__name__}: {exc}"
            trace.log(error, "error")
        finally:
            if best is not None and best.step_path is not None:
                trace.artifact(best.step_path, role="output")
            trace.end(status if output_path.is_file() else "error", error)
            if not trace.enabled:
                shutil.rmtree(work, ignore_errors=True)
        summary = {"name": name, "status": status, "error": error, "written": output_path.is_file(),
                   "attempt": best.number if best else None}
        if best is not None:
            summary.update(best.evaluation.metrics())
        return summary

    def _attempt(self, number: int, code: str, work: Path, case_dir: Path, facts: dict, timeout: float, step) -> Attempt:
        return make_attempt(number, code, work, case_dir, facts, self.settings, timeout, step)

    @staticmethod
    def _publish(attempt: Attempt, output_path: Path, trace: CaseTrace, step) -> None:
        publish(attempt, output_path, step)


def make_attempt(number: int, code: str, work: Path, case_dir: Path, facts: dict, settings: Settings, timeout: float,
                 step, base: Path | None = None) -> Attempt:
    """Write the script, build it in a subprocess, evaluate it against the facts and log everything to `step`."""
    stem = f"attempt_{number}"
    script = work / f"{stem}.py"
    script.write_text(code)
    step.artifact(script)
    box = facts.get("bounding_box_mm") or {}
    bbox = [float(box[k]) for k in "xyz"] if all(k in box for k in "xyz") else None
    report = run_script(script, work, stem, case_dir, settings, timeout, base, bbox)
    evaluation = evaluate(report, facts)
    step_path = work / report["step"] if report.get("ok") and report.get("step") else None
    if step_path is not None:
        step.artifact(step_path, role="stage")   # the dashboard's step slider walks through these solids
    for sheet in report.get("sheets") or []:
        step.artifact(work / sheet)
    for line in evaluation.lines:
        step.log(line, "info" if report.get("ok") else "error")
    step.done(output={k: v for k, v in report.items() if k not in ("traceback", "sheets")},
              metrics=evaluation.metrics())
    return Attempt(number, code, report, evaluation, step_path)


def publish(attempt: Attempt, output_path: Path, step) -> None:
    """Copy the best solid so far to the output directory, atomically."""
    if attempt.step_path is None or not attempt.step_path.is_file():
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    shutil.copyfile(attempt.step_path, tmp)
    os.replace(tmp, output_path)
    step.log(f"output updated from attempt {attempt.number} (silhouette match {attempt.evaluation.rank:.3f}, "
             f"score {attempt.evaluation.score:.3f})")


def load_facts(case_dir: Path, description: str, use_file: bool = True) -> dict:
    """facts.json when the case has one (the practice sets do, hidden parts do not), else what the brief states.
    Whatever the file leaves out is filled in from the brief."""
    facts: dict = {}
    facts_path = case_dir / "facts.json"
    if use_file and facts_path.is_file():
        try:
            facts = json.loads(facts_path.read_text())
        except json.JSONDecodeError:
            pass
    return {**facts_from_description(description), **facts}
