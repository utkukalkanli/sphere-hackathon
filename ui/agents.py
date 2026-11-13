"""What the agents of a harness run are doing, for the dashboard. Standard library only.

Reads the work directories that `agentic_cad.harness.loop` writes and changes nothing in them:

    <run>/<case>/log.jsonl          the orchestrator's events (base, agent_started, published, done ...)
    <run>/<case>/agent_<k>.jsonl    the event stream of the k-th start of the agent (`dsh --json`)
    <run>/<case>/ws/.cad/cand_<n>/  every checked candidate: part.py, candidate.stp, result.json
    <run>/<case>/ws/compare.png     the last check's picture, views of the part beside the candidate

Where runs are looked for: every directory of AGENT_DIRS (separated by the path separator), else
HARNESS_DIR (default /tmp/harness), ~/runs/harness, runs/harness, runs/_harness_sample and runs/_agents, the place to copy a work directory from another
machine to (`rsync -a vm:/tmp/harness/ runs/_agents/`). A directory that holds case folders is a run; a
directory of such directories is searched one level down. A case folder may also hold one folder per
agent (`<case>/<agent>/log.jsonl`) when several agents work on one case.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS = [Path(p).expanduser() for p in os.environ.get("AGENT_DIRS", "").split(os.pathsep) if p] or [
    Path(os.environ.get("HARNESS_DIR", "/tmp/harness")).expanduser(), Path("~/runs/harness").expanduser(),
    ROOT / "runs" / "harness", ROOT / "runs" / "_harness_sample", ROOT / "runs" / "_agents"]
QUIET_SECONDS = 90      # no event for this long: shown as quiet
DEAD_SECONDS = 600      # nothing of the whole run written for this long and no `done`: the run was stopped
# One agent can be silent for 15 minutes while the model thinks (HARNESS_IDLE_SECONDS), so a run counts as stopped
# by what all its agents write; where its container can be seen, by whether that container still runs.
TAIL_BYTES = 2_000_000  # of an agent's event stream that is read
MAX_EVENTS = 400
CLIP = 4000
LIVE = ("working", "restarting", "starting")   # states of an agent at work

_results: dict[str, dict] = {}   # result.json of a candidate never changes once it is there


def read_jsonl(path: Path, tail: int | None = None) -> list[dict]:
    try:
        with open(path, "rb") as handle:
            if tail:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(size - tail, 0))
                data = handle.read()
                if size > tail:
                    data = data.split(b"\n", 1)[-1]   # the first line is cut
            else:
                data = handle.read()
    except OSError:
        return []
    rows = []
    for line in data.splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue   # a line still being written
        if isinstance(row, dict):
            rows.append(row)
    return rows


# -- the agent's own event stream ----------------------------------------------------------------
# `dsh --json` writes one object a line (runs/_harness_sample, 2026-11-13):
#   {"type":"session"} {"type":"status","phase":"turn_start|step_start|step_end","step":n,"usage":{...}}
#   {"type":"text","text":"..."}   (a model's thinking can be inside it, between <|channel>thought and <channel|>)
#   {"type":"tool_call","callId":..,"tool":"bash|read|write|...","input":{...}}
#   {"type":"tool_result","callId":..,"status":"completed|...","result":"..."}  {"type":"error","message":".."}
# `dsh_event` reads that; any other stream falls back to `normalise`, which guesses from common keys.

THOUGHT = re.compile(r"<\|channel\|?>\s*(?:thought|analysis)\s*(.*?)(?:<\|?channel\|>|$)", re.S)
THINK_TAG = re.compile(r"<think>(.*?)(?:</think>|$)", re.S)


def _tool_line(tool: str, args: dict) -> tuple[str, str]:
    """A tool call as one line, and the long part of it (a written file) apart."""
    if not isinstance(args, dict):
        return f"{tool} {_args(args)}".strip(), ""
    if isinstance(args.get("command"), str):
        return args["command"], ""
    path = args.get("file_path") or args.get("path") or ""
    body = args.get("content") or args.get("new_string") or ""
    if tool in ("read", "view", "ls", "glob", "grep") or not body:
        return f"{tool} {path or _args(args)}".strip(), ""
    return f"{tool} {path}".strip(), str(body)


def dsh_event(row: dict) -> list[dict] | None:
    """The events of one line of `dsh --json`; None when the line is not in that format."""
    kind = row.get("type")
    if kind == "text" and isinstance(row.get("text"), str):
        text, out = row["text"], []
        for pattern in (THOUGHT, THINK_TAG):
            for found in pattern.finditer(text):
                out.append({"kind": "think", "text": found.group(1).strip()})
            text = pattern.sub("", text)
        text = re.sub(r"<\|?channel\|?>", "", text)   # an empty thought
        if text.strip():
            out.append({"kind": "say", "text": text.strip()})
        return out
    if kind == "tool_call" and "tool" in row:
        line, body = _tool_line(str(row["tool"]), row.get("input") or {})
        return [{"kind": "tool", "text": line, "tool": row["tool"], "body": body, "call": row.get("callId")}]
    if kind == "tool_result":
        status = row.get("status")
        text = _text(row.get("result")) or _args(row.get("result"))
        ok = status in (None, "completed", "ok", "success")
        if ok and re.search(r"</type>\s*<content>\s*(Created|Updated) file", text or ""):
            return []   # a file was written: the call already shows it
        if ok and (text or "").startswith("<path>"):   # a file that was read: its name only, the text folded
            path = re.search(r"<path>(.*?)</path>", text)
            return [{"kind": "result", "text": f"read {path.group(1).rsplit('/', 1)[-1] if path else ''}",
                     "body": re.sub(r"^.*?<content>\s*|\s*</content>\s*$", "", text, flags=re.S), "call": row.get("callId")}]
        return [{"kind": "result" if ok else "error", "text": text or str(status or ""), "call": row.get("callId")}]
    if kind == "error":
        return [{"kind": "error", "text": str(row.get("message") or row.get("error") or "error")}]
    if kind == "status":
        if row.get("phase") == "step_end" and isinstance(row.get("usage"), dict):
            return [{"kind": "usage", "text": "", "tokens": row["usage"].get("totalTokens") or 0,
                     "input": row["usage"].get("inputTokens") or 0}]
        return []
    if kind == "session":
        return []
    return None

NESTED = ("item", "message", "data", "payload", "delta", "part", "event", "tool_call", "toolCall", "function", "result")
TOOL_KEYS = ("tool", "toolName", "tool_name", "name")
ARG_KEYS = ("command", "cmd", "input", "arguments", "args", "params", "parameters")
TEXT_KEYS = ("text", "content", "delta", "output", "stdout", "result", "message", "summary", "error")
TYPE_KEYS = ("type", "event", "kind", "method")


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):   # parts of a message
        return "\n".join(filter(None, (_text(part.get("text") or part.get("content") or "") if isinstance(part, dict)
                                       else _text(part) for part in value)))
    return ""


def _args(value) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return value
    if isinstance(value, dict):
        for key in ("command", "cmd", "script", "code", "path", "file_path"):
            if isinstance(value.get(key), str):
                return value[key]
            if isinstance(value.get(key), list):
                return " ".join(map(str, value[key]))
        return json.dumps(value)[:CLIP]
    if isinstance(value, list):
        return " ".join(map(str, value))
    return ""


def normalise(row: dict) -> dict | None:
    """One event of the stream as {kind, text, tool, part}: kind is say, think, tool, result or error."""
    layers = [row] + [row[key] for key in NESTED if isinstance(row.get(key), dict)]
    layers += [inner[key] for inner in layers[1:] for key in NESTED if isinstance(inner.get(key), dict)]
    kinds = " ".join(str(layer[key]) for layer in layers for key in TYPE_KEYS if isinstance(layer.get(key), str)).lower()
    role = next((layer["role"] for layer in layers if isinstance(layer.get("role"), str)), "")
    args = next((_args(layer[key]) for layer in layers for key in ARG_KEYS if layer.get(key)), "")
    tool = next((layer[key] for layer in layers for key in TOOL_KEYS if isinstance(layer.get(key), str)), "")
    text = next((found for layer in layers for key in TEXT_KEYS if (found := _text(layer.get(key)))), "")
    part = "delta" in kinds or "chunk" in kinds
    is_result = any(word in kinds for word in ("result", "output", "tool_end", "toolend")) or role == "tool"
    if "error" in kinds or any(layer.get("is_error") or layer.get("isError") for layer in layers):
        kind = "error"
    elif is_result:
        kind = "result"
    elif args and (tool or any(word in kinds for word in ("tool", "function", "command", "exec", "bash"))):
        kind, text = "tool", args
    elif any(word in kinds for word in ("reason", "think")):
        kind = "think"
    elif text and role != "user" and "user" not in kinds and "system" not in kinds:
        kind = "say"
    else:
        return None
    if not text.strip():
        return None
    return {"kind": kind, "text": text, "tool": tool if kind == "tool" else "", "part": part}


def stream(path: Path, tail: int = TAIL_BYTES) -> list[dict]:
    events: list[dict] = []
    for row in read_jsonl(path, tail):
        found = dsh_event(row)
        if found is not None:
            for event in found:
                if event["kind"] == "usage":   # the size of the context, on the last step
                    if events:
                        events[-1]["tokens"] = event["input"]
                    continue
                if event["text"].strip() or event["kind"] in ("result", "error"):
                    events.append(event)
            continue
        event = normalise(row)
        if event is None:
            continue
        last = events[-1] if events else None
        if event.pop("part") and last and last["kind"] == event["kind"] and last.get("open"):
            last["text"] += event["text"]   # a text that comes in pieces
            continue
        if last:
            last.pop("open", None)
            if last["kind"] == event["kind"] and last["text"] == event["text"]:
                continue   # the same thing said as started and as finished
        event["open"] = True
        events.append(event)
    for event in events:
        event.pop("open", None)
        for key in ("text", "body"):
            if len(event.get(key) or "") > CLIP:
                event[key] = event[key][:CLIP] + " …"
    return events


# -- runs and cases ------------------------------------------------------------------------------

def _is_unit(folder: Path) -> bool:
    return (folder / "log.jsonl").is_file() or (folder / "ws").is_dir() or (folder / "hull_done").is_file()


def units(run: Path) -> list[Path]:
    """The folders of a run that hold one agent's work: a case, or an agent inside a case."""
    found = []
    for case in run.glob("test_case_*"):
        if not case.is_dir():
            continue
        inner = [d for d in case.iterdir() if d.is_dir() and d.name not in ("ws", "dsh-home") and _is_unit(d)]
        found += inner or [case]
    return sorted(found, key=lambda p: (int(re.sub(r"\D", "", p.relative_to(run).parts[0]) or 0), p.name))


def run_dirs() -> dict[str, Path]:
    """Every run under the roots, up to two levels down (`<root>/<label>/<time>/test_case_*`), named by its path."""
    found: dict[str, Path] = {}

    def add(folder: Path, name: str) -> None:
        while name in found and found[name] != folder:
            name += "_"
        found[name] = folder

    for root in ROOTS:
        if not root.is_dir():
            continue
        if any(root.glob("test_case_*")):
            add(root, root.name)
            continue
        for first in (d for d in root.iterdir() if d.is_dir()):
            if any(first.glob("test_case_*")):
                add(first, first.name)
                continue
            for second in (d for d in first.iterdir() if d.is_dir()):
                if any(second.glob("test_case_*")):
                    add(second, f"{first.name}-{second.name}")
    return found


def candidates(unit: Path) -> list[dict]:
    rows = []
    for folder in sorted((unit / "ws" / ".cad").glob("cand_*")):
        key = str(folder)
        row = _results.get(key)
        if row is None:
            try:
                result = json.loads((folder / "result.json").read_text())
                stat = (folder / "result.json").stat()
            except Exception:
                continue   # the check is still running
            measurement = result.get("measurement") or {}
            row = {"index": result.get("index"), "score": result.get("score"), "error": result.get("error"),
                   "terms": measurement.get("terms"), "volume": measurement.get("volume"),
                   "area": measurement.get("area"), "t": stat.st_mtime, "folder": folder.name}
            _results[key] = row
        rows.append(row)
    return rows


def _newest(unit: Path) -> tuple[Path | None, float]:
    """The event stream of the latest start, and when anything of the unit was last written."""
    streams = sorted(unit.glob("agent_*.jsonl"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    latest = 0.0
    for path in [*streams[-1:], unit / "log.jsonl", unit / "ws" / ".cad" / "best.json"]:
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            pass
    return (streams[-1] if streams else None), latest


_containers: tuple[float, set[str] | None] = (0.0, None)


def containers() -> set[str] | None:
    """The names of the running docker containers, or None where docker cannot be asked. Read at most every 5 s."""
    global _containers
    if time.time() - _containers[0] < 5:
        return _containers[1]
    names = None
    try:
        done = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=5)
        if done.returncode == 0:
            names = set(done.stdout.split())
    except Exception:
        pass
    _containers = (time.time(), names)
    return names


def container_of(run: Path) -> str | None:
    """forge_run.sh runs `<root>/<label>/<time>` in a container named <label>."""
    return run.parent.name if run.parent.resolve() not in {r.resolve() for r in ROOTS if r.exists()} else None


def run_activity(run: Path) -> float:
    """When anything of the run's agents was last written."""
    latest = 0.0
    for pattern in ("*/log.jsonl", "*/agent_*.jsonl", "*/previews.jsonl", "*/ws/.cad/best.json", "*/*/log.jsonl"):
        for path in run.glob(pattern):
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                pass
    return latest


def run_stopped(run: Path, now: float) -> bool:
    names, label = containers(), container_of(run)
    if names is not None and label and (run.parent / label).exists():
        if label in names:
            return False
        if now - run_activity(run) > 120:   # its container is gone (a few seconds' grace for its last writes)
            return True
    return now - run_activity(run) > DEAD_SECONDS


def stop_run(run: Path) -> str:
    """Stop the container of a run and write that into each case's log. Returns what was done."""
    label = container_of(run)
    if not label or label not in (containers() or set()):
        return "no running container"
    done = subprocess.run(["docker", "stop", "-t", "10", label], capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        return (done.stderr or "docker stop failed").strip()[-200:]
    now = time.time()
    for unit in units(run):
        if not any(e.get("event") == "done" for e in read_jsonl(unit / "log.jsonl")):
            with (unit / "log.jsonl").open("a") as handle:
                handle.write(json.dumps({"event": "done", "stopped": True, "why": "stopped from the dashboard", "t": now}) + "\n")
    global _containers
    _containers = (0.0, None)
    return "stopped"


def unit_summary(run: Path, unit: Path, now: float) -> dict:
    log = read_jsonl(unit / "log.jsonl")
    state, started, ended, starts, base, published, note, source = "waiting", None, None, 0, None, None, "", None
    for event in log:
        kind = event.get("event")
        if kind == "base":
            base, state = (event.get("measurement") or {}).get("score"), "starting"
        elif kind == "base_unreadable":
            state, note = "failed", str(event.get("why") or "")
        elif kind == "agent_started":
            state, starts, started = "working", starts + 1, started or event.get("t")
        elif kind in ("agent_hung", "agent_ended"):
            state = "restarting"
        elif kind in ("gated", "skipped"):   # no agent: the pipeline's solid was good enough, or no time was left
            state, ended = "gated" if kind == "gated" else "skipped", event.get("t")
            base = event.get("base", base)
            note = str(event.get("why") or (f"pipeline {event['base']:.3f} ≥ {event['gate']}" if kind == "gated"
                                            and event.get("base") is not None and event.get("gate") is not None else ""))
        elif kind == "plateau":
            note = "plateau"
        elif kind == "published":   # the output file was replaced: by the agent's check or the pipeline's solid
            published, source = event.get("score"), str(event.get("source") or "agent")
        elif kind == "done":
            state, ended = ("stopped" if event.get("stopped") else "done"), event.get("t")
    if (unit / "error.txt").is_file():
        state = "failed"
        try:
            note = (unit / "error.txt").read_text().strip().splitlines()[-1][:200]
        except Exception:
            pass
    latest_stream, written = _newest(unit)
    quiet = now - written if written else None
    if state in ("working", "restarting", "starting", "waiting") and run_stopped(run, now):
        state, ended = "stopped", written
    cands = candidates(unit)
    scored = [c["score"] for c in cands if c.get("score") is not None]
    doing = None
    if latest_stream is not None and state in ("working", "restarting"):
        # the last thing the agent did or said; a tool's raw output says nothing in a list
        last = next((e for e in reversed(stream(latest_stream, 200_000)) if e["kind"] in ("tool", "say", "think", "error")), None)
        if last:
            doing = {"kind": last["kind"], "text": " ".join(last["text"].split())[:160]}
    dataset = None
    try:   # which set the case came from: the agent's workspace names its input folder
        dataset = "eval" if "datasets/eval" in json.loads((unit / "ws" / ".cad" / "meta.json").read_text())["case_dir"] else "practice"
    except Exception:
        pass
    return {"id": unit.relative_to(run).as_posix(), "case": unit.relative_to(run).parts[0], "state": state, "dataset": dataset,
            "started": started, "ended": ended, "starts": starts, "base": base, "published": published,
            "source": source if published is not None else None,
            "best": max(scored) if scored else None, "checks": len(cands), "failed_checks": len(cands) - len(scored),
            "scores": [c.get("score") for c in cands], "quiet": quiet if state == "working" else None,
            "is_quiet": bool(state == "working" and quiet and quiet > QUIET_SECONDS), "doing": doing, "note": note}


def list_runs() -> dict:
    now = time.time()
    runs = []
    for name, folder in run_dirs().items():
        agents = [unit_summary(folder, unit, now) for unit in units(folder)]
        if not agents:
            continue
        times = [a["started"] for a in agents if a["started"]]
        runs.append({"id": name, "path": str(folder), "agents": agents, "started": min(times) if times else None,
                     "live": any(a["state"] in ("working", "restarting", "starting") for a in agents)})
    runs.sort(key=lambda r: (r["live"], r["started"] or 0, r["id"]), reverse=True)
    return {"runs": runs, "now": now, "roots": [str(root) for root in ROOTS]}


def unit_dir(run: str, unit: str) -> Path | None:
    folder = run_dirs().get(run)
    if folder is None:
        return None
    target = (folder / unit).resolve()
    return target if target.is_relative_to(folder.resolve()) and target.is_dir() else None


def detail(run: str, unit: str) -> dict | None:
    folder = unit_dir(run, unit)
    if folder is None:
        return None
    events = []
    for index, path in enumerate(sorted(folder.glob("agent_*.jsonl"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))):
        if index:
            events.append({"kind": "start", "text": f"start {index + 1}", "tool": ""})
        events += stream(path)
    workspace = folder / "ws"
    files = {name: (workspace / name).stat().st_mtime_ns for name in ("compare.png", "views.png", "best_part.py")
             if (workspace / name).is_file()}
    best = None
    try:
        best = json.loads((workspace / ".cad" / "best.json").read_text()).get("index")
    except Exception:
        pass
    return {"summary": unit_summary(run_dirs()[run], folder, time.time()), "skipped": max(len(events) - MAX_EVENTS, 0),
            "events": events[-MAX_EVENTS:], "log": read_jsonl(folder / "log.jsonl"), "candidates": candidates(folder),
            "best": best, "files": files}


def file(run: str, rest: str) -> Path | None:
    """A file of a run, for /files/agents/<run>/<rest>. Never the agent's home (it holds the key's profile)."""
    folder = run_dirs().get(run)
    if folder is None or "dsh-home" in Path(rest).parts:
        return None
    target = (folder / rest).resolve()
    return target if target.is_relative_to(folder.resolve()) and target.is_file() else None


# -- an agent run as a run of the main view ---------------------------------------------------------
# A harness run is listed beside the traced runs as `agents-<name>`. Its events are made up from the work
# directory in the format of agentic_cad.trace, so the main view shows it step by step: first the pipeline
# (its own traced steps when a traced run started the agents, else one stop with the solid the agent was
# given), then one stop per check the agent made, with that check's solid. Failed checks have no solid:
# what was tried is folded into the next check that went through.

PREFIX = "agents-"


def started_at(unit: Path) -> float | None:
    times = [e["t"] for e in read_jsonl(unit / "log.jsonl") if isinstance(e.get("t"), (int, float))]
    return min(times) if times else None


def _log(step: str, event: dict, t: float) -> dict | None:
    text = " ".join(event["text"].split())
    if event["kind"] in ("say", "think") and text:
        return {"t": "log", "step": step, "ts": t, "level": "info", "msg": text[:400]}
    if event["kind"] == "tool":
        return {"t": "log", "step": step, "ts": t, "level": "info", "msg": f"› {text[:300]}"}
    if event["kind"] in ("error", "start"):
        return {"t": "log", "step": step, "ts": t, "level": "warn", "msg": text[:300]}
    return None


def case_events(unit: Path, true_mm3: float | None, pipeline: list[dict] | None) -> list[dict]:
    """The events of one case of an agent run. `pipeline`: the traced run's own events for this case, if any."""
    summary = unit_summary(unit.parent, unit, time.time())
    t0 = started_at(unit) or unit.stat().st_mtime
    log_rows = read_jsonl(unit / "log.jsonl")
    volume = lambda ratio: round(ratio * true_mm3 / 1000, 3) if ratio and true_mm3 else None   # noqa: E731
    events: list[dict] = []
    if pipeline:
        events += [e for e in pipeline if e.get("t") != "case_end"]
    else:
        events.append({"t": "case_start", "ts": t0})
        base = next((p for p in (unit / "ws" / "base.step", unit / "base.step") if p.is_file()), None)
        given = unit / "ws" / "base.step"
        measured = next((e.get("measurement") or {} for e in log_rows if e.get("event") == "base"), {})
        events.append({"t": "step_start", "id": "pipeline", "name": "pipeline" if given.is_file() else "bounding_box",
                       "kind": "pipeline", "ts": t0})
        if base is not None:
            events.append({"t": "artifact", "step": "pipeline", "name": "base.step", "path": base.relative_to(unit).as_posix(),
                           "role": "stage", "ts": t0})
        events.append({"t": "step_end", "id": "pipeline", "status": "ok", "ts": t0,
                       "metrics": {k: v for k, v in {"volume_cm3": volume(measured.get("volume")),
                                                     "local_score": measured.get("score")}.items() if v is not None}})
    if summary["state"] == "gated":
        events.append({"t": "step_start", "id": "gate", "name": "agent_not_needed", "kind": "agent", "ts": t0,
                       "meta": {"label": "not needed", "description": "The pipeline's solid scored above the gate, so no agent was started"}})
        events.append({"t": "log", "step": "gate", "ts": t0, "level": "info", "msg": summary["note"]})
        events.append({"t": "step_end", "id": "gate", "status": "ok", "ts": t0})
    # The agent's own steps, as they happened: every check (with its solid, or failed), every time the agent was
    # started again, and while it works what it does now
    cands = sorted(candidates(unit), key=lambda c: c["index"])
    published = {e.get("check") for e in log_rows if e.get("event") == "published"}
    stops = [e for e in log_rows if e.get("event") in ("agent_hung", "agent_ended")]
    streams = sorted(unit.glob("agent_*.jsonl"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    newest = max((c for c in cands if c.get("score") is not None), key=lambda c: c["t"], default=None)
    at, ci, seq = summary["started"] or t0, 0, 0
    live = summary["state"] in LIVE
    planned: list[tuple] = []

    def emit(*args, **extra) -> None:   # in order of time, after the previews are known (below)
        planned.append((args, extra))

    # Every stop has a key of its own (the check's number, the start's number, the preview's number): a draft put in
    # later between two checks must not renumber the stops, or a stop the reader chose would turn into another
    def write(label: str, description: str, segment: list[dict], end: float | None, status: str | None, key: str,
              kind: str = "agent", **extra) -> str:
        nonlocal seq, at
        seq += 1
        sid = f"agent-{key}"
        events.append({"t": "step_start", "id": sid, "name": f"agent_{key}", "kind": kind, "ts": at,
                       "meta": {"label": label, "description": description}})
        for event in segment:
            line = _log(sid, event, at)
            if line:
                events.append(line)
        for artifact in extra.pop("artifacts", []):
            events.append({"t": "artifact", "step": sid, "ts": end or at, **artifact})
        if status:
            events.append({"t": "step_end", "id": sid, "status": status, "ts": end or at, **extra})
            at = end or at
        return sid

    for k, path in enumerate(streams):
        segment, waiting = [], False
        for event in stream(path):
            segment.append(event)
            if event["kind"] == "tool" and re.search(r"(^|[\s/;&])\.?/?check(\s|$)", event["text"]):
                waiting = True
            elif waiting and event["kind"] in ("result", "error") and ci < len(cands):
                cand, ci, waiting = cands[ci], ci + 1, False
                if cand.get("score") is None:
                    reason = (cand.get("error") or "").strip().splitlines()
                    emit(f"check {cand['index'] + 1}", "A check whose script gave no solid", segment, cand["t"], "error",
                         key=f"check{cand['index']}",
                         error=" ".join(reason[:1] + reason[-1:]) if reason else "no solid")
                else:
                    terms = cand.get("terms") or {}
                    metrics = {"local_score": round(cand["score"], 4), "volume_cm3": volume(cand.get("volume")),
                               **{f"{n}_term": round(terms[n], 3) for n in ("volume", "surface", "dimensions", "holes") if n in terms}}
                    artifacts = [{"name": "candidate.stp", "role": "stage", "path": f"ws/.cad/{cand['folder']}/candidate.stp"}]
                    if cand is newest and (unit / "ws" / "compare.png").is_file():
                        artifacts.append({"name": "compare.png", "path": "ws/compare.png"})
                    emit(f"check {cand['index'] + 1}", "A check: the solid the agent's CadQuery script made, with its local score",
                         segment, cand["t"], "ok", key=f"check{cand['index']}", artifacts=artifacts,
                         metrics={k: v for k, v in metrics.items() if v is not None},
                         output={"written to the output file": True} if cand["index"] in published else None)
                segment = []
        if k < len(streams) - 1:   # this start ended: the agent was started again
            stop = stops[k] if k < len(stops) else {}
            hung = stop.get("event") == "agent_hung" or any(e.get("event") == "agent_hung" and e.get("starts") == k + 1 for e in stops)
            emit("restarted", "The agent was stopped and started again; what it had in mind is lost, its files stay",
                 segment, stop.get("t"), "error", key=f"restart{k + 1}",
                 error="no answer from the model for 4 minutes" if hung else "the agent stopped by itself")
        elif live:
            emit("working", "What the agent does now; a solid appears when it checks one", segment, None, None, key="now")
        elif segment:
            emit("stopped", "What the agent did after its last check", segment, summary["ended"], "ok", key="end")

    # the solid of every script the agent saved (HARNESS_PREVIEW), unless a check of the same script shows it
    checked = set()
    for cand in cands:
        try:
            checked.add(hashlib.sha1((unit / "ws" / ".cad" / cand["folder"] / "part.py").read_bytes()).hexdigest())
        except OSError:
            pass
    drafts = 0
    for row in read_jsonl(unit / "previews.jsonl"):
        if not row.get("ok") or row.get("sha") in checked:
            continue
        emit("draft", f"The solid of {row.get('script', 'a script')} as the agent saved it; not checked or scored",
             [], row.get("t"), "ok", key=f"draft{row['index']}", artifacts=[{"name": f"{row['index']:03d}.stp", "role": "stage",
                                                 "path": f"previews/{row['index']:03d}.stp"}])
    # the pipeline beside the agent (HARNESS_PARALLEL): its finished solid, measured like a check
    for row in log_rows:
        if row.get("event") == "pipeline" and row.get("measurement"):
            score = row["measurement"].get("score")
            hollow_note = [{"kind": "say", "text": "Hollow part: the local score cannot judge it, so the pipeline's solid is "
                            "kept whatever the agent scores (practice 6: this rule's solid scored 0.996 for real; an agent "
                            "solid with the same volume, area and local score 0.967 scored 0.756)."}] if row.get("hollow") else []
            emit("pipeline done (hollow)" if row.get("hollow") else "pipeline done",
                 "The pipeline, working beside the agent, finished its solid; measured like a check",
                 hollow_note, row.get("t"), "ok", key="pipeline-done", kind="pipeline",
                 artifacts=[{"name": "pipeline.stp", "role": "stage", "path": "pipeline.stp"}] if (unit / "pipeline.stp").is_file() else [],
                 metrics={"local_score": round(score, 4)} if score is not None else {})
    # what the output file holds in the end, so that the last stop shows the case's result
    final = [e for e in log_rows if e.get("event") == "published"]
    if final and not live:
        last = final[-1]
        source = str(last.get("source") or "agent")
        cand = next((c for c in cands if c["index"] == last.get("check")), None)
        path = (f"ws/.cad/{cand['folder']}/candidate.stp" if cand and source == "agent"
                else "pipeline.stp" if (unit / "pipeline.stp").is_file() else None)
        hollow = "void" in source
        # a part the pipeline hollowed keeps the pipeline's solid whatever the agent scores: no view, and so no local
        # score, sees an inner void (practice 6 on the leaderboard: the agent's solid local 0.967, real 0.756; the
        # pipeline's hollow one local 0.859, real 0.996)
        why = ([{"kind": "say", "text": "The pipeline found this part hollow, so its solid is kept whatever the agent scored: "
                 "no view and no local score can see an inner void, and here the lower local score is the better answer "
                 "(practice 6 on the leaderboard: agent 0.967 local, 0.756 real; hollow pipeline 0.859 local, 0.996 real)."}]
               if hollow else [])
        emit("output (hollow)" if hollow else "output",
             f"What the output file holds at the end: {'the agent' if source == 'agent' else 'the pipeline'}'s solid"
             + (f" (check {last['check'] + 1})" if source == "agent" and last.get("check") is not None else f" ({source})"),
             why, (summary["ended"] or last.get("t") or at) + 0.001, "ok", key="output",
             kind="agent" if source == "agent" else "pipeline",
             artifacts=[{"name": "output.stp", "role": "stage", "path": path}] if path else [],
             metrics={"local_score": round(last["score"], 4)} if last.get("score") is not None else {},
             output={"made by": source})
    planned.sort(key=lambda p: p[0][3] if p[0][3] is not None else float("inf"))   # by end time; the live stop last
    for args, extra in planned:   # drafts numbered in the order they were made
        if args[0] == "draft":
            drafts += 1
            args = (f"draft {drafts}",) + args[1:]
            planned[planned.index((("draft",) + args[1:], extra))] = (args, extra)
    for args, extra in planned:
        write(*args, **extra)
    # Which stop's solid is in the output file now: the one whose number the case list shows
    final = [e for e in log_rows if e.get("event") == "published"]
    if any(e.get("id") == "agent-output" for e in events if e["t"] == "step_start"):
        holder = "agent-output"
    elif final and str(final[-1].get("source") or "agent") == "agent" and final[-1].get("check") is not None:
        holder = f"agent-check{final[-1]['check']}"
    elif final:
        holder = "agent-pipeline-done"
    else:
        holder = "pipeline"   # nothing published yet: the file is still what the pipeline (or the box) wrote
    for event in events:
        if event["t"] == "step_start" and event["id"] == holder:
            event.setdefault("meta", {})["in_file"] = True
    if summary["state"] not in LIVE and summary["state"] != "waiting":
        events.append({"t": "case_end", "ts": summary["ended"] or start,
                       "status": "error" if summary["state"] == "failed" else "ok"})
    return events
