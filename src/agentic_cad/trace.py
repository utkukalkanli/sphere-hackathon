"""Writes the traces the local dashboard (ui/) reads: runs/<run>/run.json and one events.jsonl per case.

Enabled with TRACE=1 (the dashboard sets TRACE, TRACE_RUN_ID and TRACE_DIR when it launches a run).
When disabled every call is a no-op and nothing is written, which is how the submitted image runs.

    case = tracer.case("test_case_1")
    with case.step("generate", kind="llm", input={...}) as step:
        step.log("...")
        step.artifact(path)              # a file inside case.dir
        step.done(output=..., metrics={...})
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _json_safe(value, limit: int = 20000):
    try:
        text = json.dumps(value, default=str)
    except Exception:
        text = json.dumps(str(value))
    if len(text) > limit:
        return str(value)[:limit] + " …[truncated]"
    return json.loads(text)


class Step:
    def __init__(self, case: "CaseTrace", step_id: str | None):
        self.case, self.id = case, step_id
        self._result: dict = {}

    def log(self, msg: str, level: str = "info") -> None:
        self.case._emit({"t": "log", "step": self.id, "level": level, "msg": str(msg)})

    def artifact(self, path: Path, role: str | None = None) -> None:
        self.case.artifact(path, role=role, step=self.id)

    def done(self, output=None, metrics: dict | None = None) -> None:
        if output is not None:
            self._result["output"] = _json_safe(output)
        if metrics:
            self._result["metrics"] = _json_safe(metrics)


class CaseTrace:
    def __init__(self, directory: Path | None):
        self.dir = directory                      # None when tracing is off
        self._lock = threading.Lock()
        self._stack = threading.local()
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.dir is not None

    def _emit(self, event: dict) -> None:
        if self.dir is None:
            return
        event = {"ts": time.time(), **event}
        with self._lock, open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def start(self) -> None:
        self._emit({"t": "case_start"})

    def end(self, status: str = "ok", error: str | None = None) -> None:
        self._emit({"t": "case_end", "status": status, "error": error})

    def log(self, msg: str, level: str = "info") -> None:
        self._emit({"t": "log", "step": None, "level": level, "msg": str(msg)})

    def artifact(self, path: Path, role: str | None = None, step: str | None = None) -> None:
        if self.dir is None or not Path(path).is_file():
            return
        path = Path(path)
        try:
            relative = path.resolve().relative_to(self.dir.resolve()).as_posix()
        except ValueError:
            return   # only files inside the case directory can be served by the dashboard
        self._emit({"t": "artifact", "step": step, "name": path.name, "path": relative,
                    "size": path.stat().st_size, "role": role})

    @contextmanager
    def step(self, name: str, kind: str = "tool", input=None, meta: dict | None = None):
        """kind: llm | code | check | tool | io (the dashboard colours steps by kind)."""
        if self.dir is None:
            yield Step(self, None)
            return
        stack = getattr(self._stack, "ids", None)
        if stack is None:
            stack = self._stack.ids = []
        step = Step(self, uuid.uuid4().hex[:12])
        event = {"t": "step_start", "id": step.id, "parent": stack[-1] if stack else None, "name": name, "kind": kind}
        if input is not None:
            event["input"] = _json_safe(input)
        if meta:
            event["meta"] = _json_safe(meta)
        self._emit(event)
        stack.append(step.id)
        try:
            yield step
        except BaseException as exc:
            self._emit({"t": "step_end", "id": step.id, "status": "error", "error": f"{type(exc).__name__}: {exc}",
                        **step._result})
            raise
        else:
            self._emit({"t": "step_end", "id": step.id, "status": "ok", **step._result})
        finally:
            stack.pop()


class Tracer:
    def __init__(self, meta: dict | None = None):
        enabled = os.environ.get("TRACE", "").strip().lower() in ("1", "true", "yes", "on")
        self.run_dir: Path | None = None
        if enabled:
            root = Path(os.environ.get("TRACE_DIR") or REPO_ROOT / "runs")
            run_id = os.environ.get("TRACE_RUN_ID") or datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                self.run_dir = root / run_id
                self.run_dir.mkdir(parents=True, exist_ok=True)
                self._update({"id": run_id, "status": "running", "started_at": time.time(), "pid": os.getpid(),
                              "meta": meta or {}}, keep_existing=("started_at", "launched_by", "argv", "only",
                                                                  "target", "dataset"))
            except OSError:
                self.run_dir = None   # read-only filesystem: run without traces

    @property
    def enabled(self) -> bool:
        return self.run_dir is not None

    def _update(self, values: dict, keep_existing: tuple = ()) -> None:
        path = self.run_dir / "run.json"
        try:
            current = json.loads(path.read_text())
        except Exception:
            current = {}
        merged = {**current, **values}
        for key in keep_existing:
            if key in current:
                merged[key] = current[key]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, default=str))
        tmp.replace(path)

    def case(self, name: str) -> CaseTrace:
        return CaseTrace(self.run_dir / name if self.run_dir else None)

    def finish(self, status: str = "ok") -> None:
        if self.run_dir is not None:
            self._update({"status": status, "ended_at": time.time()})
