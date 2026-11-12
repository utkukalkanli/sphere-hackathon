"""Run tracing for the agent pipeline: what the dashboard in ui/ reads.

Zero dependencies, thread-safe, and it never raises: if the trace directory can't be written
(e.g. inside the submission container), tracing silently turns itself off.

    from agentic_cad import trace

    tracer = trace.Tracer()                       # one per pipeline run
    with tracer.case("test_case_1") as case:      # one per test case
        with trace.step("plan", kind="llm", input={"prompt": prompt}) as s:
            s.log("calling the model")
            s.attach_file(view_png)               # image / code / .stp, shown in the UI
            s.attach("model.py", code)
            s.metric(tokens=1234)
            s.set_output(answer)
        case.output(output_path)                  # the final .stp of this case
    tracer.finish()

`trace.step / log / attach / attach_file` use the case and step that are active in the current
context, so code deep inside the agent doesn't need the objects passed down. Steps nest. In a worker
thread, pass `parent=` (and use `case.step`) since context doesn't cross thread pools by itself.

Layout on disk (TRACE_DIR, default <repo>/runs):

    runs/<run_id>/run.json
    runs/<run_id>/<test_case>/events.jsonl
    runs/<run_id>/<test_case>/files/...

Environment: TRACE=0 disables, TRACE_DIR moves the directory, TRACE_RUN_ID names the run.
"""

from __future__ import annotations

import contextvars
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_STRING = 400_000  # characters kept per string value in an event

KINDS = ("llm", "tool", "code", "check", "io", "other")

_current_case: contextvars.ContextVar["CaseTrace | None"] = contextvars.ContextVar("trace_case", default=None)
_current_step: contextvars.ContextVar["Step | None"] = contextvars.ContextVar("trace_step", default=None)


def _clip(value: Any) -> Any:
    """Make a value JSON-safe and bounded."""
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            return value[:MAX_STRING] + f"\n… [{len(value) - MAX_STRING} more characters clipped]"
        return value
    if isinstance(value, dict):
        return {str(k): _clip(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clip(v) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return str(value)


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=3
        )
        return out.stdout.strip() or None
    except Exception:
        return None


class Tracer:
    """One pipeline run."""

    def __init__(self, meta: dict | None = None, enabled: bool | None = None):
        self.dir: Path | None = None
        self.run_id = os.environ.get("TRACE_RUN_ID") or (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        )
        self._info: dict = {}
        if enabled is None:
            enabled = os.environ.get("TRACE", "1") != "0"
        if not enabled:
            return
        candidates = []
        if os.environ.get("TRACE_DIR"):
            candidates.append(Path(os.environ["TRACE_DIR"]))
        elif (REPO_ROOT / "pyproject.toml").is_file():
            candidates.append(REPO_ROOT / "runs")
        candidates.append(Path(tempfile.gettempdir()) / "agentic_cad_runs")
        for base in candidates:
            try:
                run_dir = base / self.run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                self.dir = run_dir
                break
            except Exception:
                continue
        if self.dir is None:
            return
        try:
            previous = {}
            if (self.dir / "run.json").is_file():  # pre-created by the dashboard when it launches a run
                previous = json.loads((self.dir / "run.json").read_text())
            self._info = {
                **previous,
                "id": self.run_id,
                "started_at": previous.get("started_at") or time.time(),
                "status": "running",
                "pid": os.getpid(),
                "argv": sys.argv,
                "commit": _git_commit(),
                "model": os.environ.get("OPENAI_MODEL") or None,
                "meta": _clip(meta or {}),
            }
            _write_json(self.dir / "run.json", self._info)
        except Exception:
            self.dir = None

    def case(self, name: str) -> "CaseTrace":
        return CaseTrace(self, name)

    @classmethod
    def attached(cls, run_dir: Path) -> "Tracer":
        """The tracer of a run that another process started: cases can be written into its
        directory, the run's own record (run.json) is left to that process."""
        tracer = cls.__new__(cls)
        tracer.dir, tracer.run_id, tracer._info = Path(run_dir), Path(run_dir).name, {}
        return tracer

    def finish(self, status: str = "ok", error: str | None = None) -> None:
        if self.dir is None:
            return
        try:
            self._info.update(status=status, ended_at=time.time())
            if error:
                self._info["error"] = error
            _write_json(self.dir / "run.json", self._info)
        except Exception:
            pass

    def __enter__(self) -> "Tracer":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.finish("ok")
        else:
            self.finish("error", "".join(traceback.format_exception(exc_type, exc, tb)))
        return False


class CaseTrace:
    """The trace of one test case within a run."""

    def __init__(self, tracer: Tracer | None, name: str):
        self.name = name
        self.dir: Path | None = None
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._files = itertools.count(1)
        self._tokens: list = []
        # A case may be entered several times (the hull of every case first, the later steps after):
        # keep this object, set `more` while it will be entered again, and its end is recorded once
        self.more = False
        self._started = False
        if tracer is not None and tracer.dir is not None:
            try:
                self.dir = tracer.dir / name
                (self.dir / "files").mkdir(parents=True, exist_ok=True)
            except Exception:
                self.dir = None

    # -- low level -----------------------------------------------------------------------------

    def _emit(self, event: dict) -> None:
        if self.dir is None:
            return
        try:
            event = {"ts": time.time(), **event}
            line = json.dumps(_clip(event), default=str)
            with self._lock:
                with open(self.dir / "events.jsonl", "a") as f:
                    f.write(line + "\n")
        except Exception:
            pass

    def _store(self, name: str, content: str | bytes | None = None, source: Path | None = None) -> dict | None:
        if self.dir is None:
            return None
        try:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "file"
            target = self.dir / "files" / f"{next(self._files):03d}_{safe}"
            if source is not None:
                shutil.copyfile(source, target)
            elif isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content if isinstance(content, str) else json.dumps(content, indent=1, default=str))
            return {"name": name, "path": f"files/{target.name}", "size": target.stat().st_size}
        except Exception:
            return None

    def _attach(self, step_id: str | None, name: str, content=None, source=None, role: str | None = None) -> None:
        stored = self._store(name, content=content, source=source)
        if stored:
            self._emit({"t": "artifact", "step": step_id, **stored, **({"role": role} if role else {})})

    # -- public --------------------------------------------------------------------------------

    def step(self, name: str, kind: str = "other", input: Any = None, parent: "Step | None" = None, **meta) -> "Step":
        return Step(self, name, kind, input, parent, meta)

    def plan(self, steps: list[dict]) -> None:
        """The steps this case is going to run, in order, as {"name": ..., "off": reason or None}. The
        dashboard shows them all from the start, before any has run."""
        self._emit({"t": "plan", "steps": steps})

    def skipped(self, name: str, reason: str) -> None:
        """A planned step that will not run in this case, and why."""
        self._emit({"t": "skip", "name": name, "reason": reason})

    def log(self, message: Any, level: str = "info") -> None:
        step = _current_step.get()
        self._emit({"t": "log", "step": step.id if step and step.case is self else None, "level": level,
                    "msg": str(message)})

    def attach(self, name: str, content: str | bytes | dict | list) -> None:
        """Save text / bytes / JSON as a file of this case (not tied to a step)."""
        self._attach(None, name, content=content)

    def attach_file(self, path: str | Path, name: str | None = None) -> None:
        self._attach(None, name or Path(path).name, source=Path(path))

    def output(self, path: str | Path) -> None:
        """Record the final .stp of this case, so the dashboard can show and compare it."""
        path = Path(path)
        if path.is_file():
            self._attach(None, path.name, source=path, role="output")
        else:
            self.log(f"output {path} was not written", level="error")

    def __enter__(self) -> "CaseTrace":
        self._tokens.append((_current_case.set(self), _current_step.set(None)))
        if not self._started:
            self._emit({"t": "case_start", "name": self.name})
            self._started = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        event: dict = {"t": "case_end", "status": "ok" if exc_type is None else "error"}
        if exc_type is not None:
            event["error"] = "".join(traceback.format_exception(exc_type, exc, tb))
        if exc_type is not None or not self.more:
            self._emit(event)
        try:
            case_token, step_token = self._tokens.pop()
            _current_step.reset(step_token)
            _current_case.reset(case_token)
        except Exception:
            pass
        return False


class Step:
    """One step of the agent: an LLM call, a tool call, running generated code, a check, ..."""

    def __init__(self, case: CaseTrace, name: str, kind: str, input: Any, parent: "Step | None", meta: dict):
        self.case = case
        self.name = name
        self.kind = kind if kind in KINDS else "other"
        self.id = f"s{next(case._ids)}"
        self._input = input
        self._parent = parent
        self._meta = meta
        self._output: Any = None
        self._metrics: dict = {}
        self._status: str | None = None
        self._token = None

    def __enter__(self) -> "Step":
        parent = self._parent
        if parent is None:
            current = _current_step.get()
            parent = current if current is not None and current.case is self.case else None
        event = {"t": "step_start", "id": self.id, "parent": parent.id if parent else None, "name": self.name,
                 "kind": self.kind}
        if self._input is not None:
            event["input"] = self._input
        if self._meta:
            event["meta"] = self._meta
        self.case._emit(event)
        self._token = _current_step.set(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        event: dict = {"t": "step_end", "id": self.id,
                       "status": self._status or ("ok" if exc_type is None else "error")}
        if self._output is not None:
            event["output"] = self._output
        if self._metrics:
            event["metrics"] = self._metrics
        if exc_type is not None:
            event["error"] = "".join(traceback.format_exception(exc_type, exc, tb))
        self.case._emit(event)
        try:
            _current_step.reset(self._token)
        except Exception:
            pass
        return False

    def log(self, message: Any, level: str = "info") -> None:
        self.case._emit({"t": "log", "step": self.id, "level": level, "msg": str(message)})

    def set_output(self, value: Any) -> None:
        self._output = value

    def metric(self, **values) -> None:
        """Numbers / short strings shown as chips on the step: tokens=..., score=..., iteration=..."""
        self._metrics.update(values)

    def fail(self, reason: Any = None) -> None:
        """Mark the step as failed without raising (e.g. a check that didn't pass)."""
        self._status = "error"
        if reason is not None:
            self.log(reason, level="error")

    def attach(self, name: str, content: str | bytes | dict | list, role: str | None = None) -> None:
        self.case._attach(self.id, name, content=content, role=role)

    def attach_file(self, path: str | Path, name: str | None = None, role: str | None = None) -> None:
        """role="stage" marks an intermediate .stp: the dashboard lets you step through those."""
        self.case._attach(self.id, name or Path(path).name, source=Path(path), role=role)


_NULL_CASE = CaseTrace(None, "untraced")


def current_case() -> CaseTrace:
    """The active case, or a no-op one when nothing is being traced."""
    return _current_case.get() or _NULL_CASE


def step(name: str, kind: str = "other", input: Any = None, **meta) -> Step:
    return current_case().step(name, kind=kind, input=input, **meta)


def plan(steps: list[dict]) -> None:
    current_case().plan(steps)


def skipped(name: str, reason: str) -> None:
    current_case().skipped(name, reason)


def log(message: Any, level: str = "info") -> None:
    current_case().log(message, level)


def attach(name: str, content: str | bytes | dict | list) -> None:
    target = _current_step.get()
    (target or current_case()).attach(name, content)


def attach_file(path: str | Path, name: str | None = None) -> None:
    target = _current_step.get()
    (target or current_case()).attach_file(path, name)
