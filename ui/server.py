"""Local dashboard for the Prompt-to-CAD pipeline. Standard library only.

    uv run python ui/server.py            # http://localhost:8765

Shows the test cases of the practice set (artifacts/inputs) and the local eval set (datasets/eval:
brief, views, facts, expected .stp), the traces written by agentic_cad.trace under runs/, and the
output .stp files next to what was expected. It can also launch scripts/main.py and pytest. Development tool only:
it is not part of the submitted image (ui/ is in .dockerignore) and listens on localhost.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents  # noqa: E402  (ui/agents.py: what the agents of a harness run are doing)

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
ARTIFACTS = ROOT / "artifacts"
INPUTS = ARTIFACTS / "inputs"
OUTPUTS = ARTIFACTS / "outputs"
EXAMPLES = ARTIFACTS / "example_outputs"
EVAL = ROOT / "datasets" / "eval"
RUNS = Path(os.environ.get("TRACE_DIR") or ROOT / "runs").resolve()

# A dataset is a directory of test_case_<n>/ inputs, optionally with the expected .stp of each case.
# `outputs` is where committed outputs live (only the practice set is committed and scored).
DATASETS = {
    "practice": {"label": "Practice set", "inputs": INPUTS, "inputs_url": "/files/artifacts/inputs",
                 "truth": None, "truth_url": None, "outputs": OUTPUTS, "index": None},
    "eval": {"label": "Local eval", "inputs": EVAL / "inputs", "inputs_url": "/files/eval/inputs",
             "truth": EVAL / "ground_truth", "truth_url": "/files/eval/ground_truth", "outputs": None,
             "index": EVAL / "index.csv"},
}
FILE_ROOTS = {"artifacts": ARTIFACTS, "eval": EVAL, "runs": RUNS}

NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")
PROCS: dict[str, subprocess.Popen] = {}
_summary_cache: dict[str, tuple[tuple, dict]] = {}
_pytest_lock = threading.Lock()


def case_number(name: str) -> int:
    match = re.search(r"(\d+)$", name)
    return int(match.group(1)) if match else 0


def file_info(path: Path) -> dict | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return {"size": stat.st_size, "mtime": stat.st_mtime}


# -- test cases --------------------------------------------------------------------------------

def read_index(path: Path | None) -> dict[str, dict]:
    """datasets/eval/index.csv: tier and source of each case."""
    rows = {}
    if path and path.is_file():
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                rows[f"test_case_{row.get('n')}"] = row
    return rows


def list_datasets() -> list[dict]:
    result = []
    for key, ds in DATASETS.items():
        if ds["inputs"].is_dir():
            count = sum(1 for d in ds["inputs"].glob("test_case_*") if (d / "description.md").is_file())
            result.append({"id": key, "label": ds["label"], "count": count, "has_truth": bool(ds["truth"]),
                           "committed": bool(ds["outputs"]), "inputs_url": ds["inputs_url"]})
    return result


def list_cases(dataset: str) -> list[dict]:
    ds = DATASETS[dataset]
    index = read_index(ds["index"])
    cases = []
    for case_dir in ds["inputs"].glob("test_case_*"):
        description = case_dir / "description.md"
        if not description.is_file():
            continue
        text = description.read_text(errors="replace")
        name = case_dir.name
        row = index.get(name, {})
        title = row.get("name") or next(
            (line[2:].strip() for line in text.splitlines() if line.startswith("# ")), name)
        facts = None
        try:
            facts = json.loads((case_dir / "facts.json").read_text())
        except Exception:
            pass
        truth = file_info(ds["truth"] / f"{name}.stp") if ds["truth"] else None
        if truth:
            truth["url"] = f"{ds['truth_url']}/{name}.stp"
        output = file_info(ds["outputs"] / f"{name}.stp") if ds["outputs"] else None
        if output:
            output["url"] = f"/files/artifacts/outputs/{name}.stp"
        cases.append({
            "name": name,
            "number": case_number(name),
            "title": title,
            "tier": row.get("tier") or None,
            "source": row.get("source") or None,
            "views": sorted(p.name for p in (case_dir / "images").glob("view_*.png")),
            "context": (case_dir / "context.png").is_file(),
            "facts": facts,
            "truth": truth,
            "output": output,
        })
    return add_scores(dataset, sorted(cases, key=lambda c: c["number"]))


def eval_scores() -> dict[str, dict]:
    """Newest result of tools/eval_hull.py per case: the score after each step and the sampled IoU."""
    best: dict[str, tuple] = {}  # the run with the most steps (not an ablation), then the newest
    for path in (RUNS / "_eval_cache").glob("result_*_test_case_*.json"):
        try:
            row = json.loads(path.read_text())
            true = float(row["true"])
            steps = [round(min(v, true) / max(v, true), 4) for v in row["volumes"].values() if v and true]
            rank = (len(steps), path.stat().st_mtime)
        except Exception:
            continue
        if steps and (row["name"] not in best or rank > best[row["name"]][0]):
            best[row["name"]] = (rank, {"score": row.get("iou", steps[-1]), "steps": steps, "iou": "iou" in row,
                                        "at": path.stat().st_mtime, "by": "pipeline"})
    return {name: score for name, (_, score) in best.items()}


def run_scores(dataset: str) -> dict[str, dict]:
    """Volume score after each step of the newest traced run of every case, and the newest agent run's result."""
    scores: dict[str, dict] = {}
    for run in list_runs():  # newest first
        for case in run["cases"]:
            if case["name"] in scores or (case.get("dataset") or run.get("dataset")) != dataset:
                continue
            if run.get("agents"):
                if case.get("score") is not None:
                    scores[case["name"]] = {"agent": case["score"], "by": case["by"], "at": run.get("started_at") or 0,
                                            "hollow": case.get("hollow", False)}
                continue
            volumes = [s["volume_cm3"] for s in case.get("stages", []) if s.get("volume_cm3")]
            if volumes:
                scores[case["name"]] = {"cm3": volumes, "at": run.get("started_at") or 0}
    return scores


def add_scores(dataset: str, cases: list[dict]) -> list[dict]:
    """`score` per case: 1 is the part itself. The eval cache where it has the case, else the traces."""
    cached = eval_scores() if DATASETS[dataset]["truth"] else {}
    traced = run_scores(dataset)
    for case in cases:   # the newest result: the eval cache, a traced run or an agent run
        score = cached.get(case["name"])
        true = ((case.get("facts") or {}).get("volume_mm3") or 0) / 1000
        found = traced.get(case["name"])
        if found and (score is None or found["at"] >= score.get("at", 0)):
            if "agent" in found:
                score = {"score": found["agent"], "steps": [found["agent"]], "iou": False, "by": found["by"],
                         "kind": "local", "at": found["at"], "hollow": found.get("hollow", False)}
            elif true:
                steps = [round(min(v, true) / max(v, true), 4) for v in found["cm3"]]
                score = {"score": steps[-1], "steps": steps, "iou": False, "by": "pipeline", "at": found["at"]}
        case["score"] = score
    return cases


def dataset_of(info: dict) -> str | None:
    """Which dataset a run was made on: recorded by the dashboard, else derived from --input_dir."""
    if info.get("dataset") in DATASETS:
        return info["dataset"]
    input_dir = (info.get("meta") or {}).get("input_dir")
    if input_dir:
        try:
            resolved = Path(input_dir).resolve()
            for key, ds in DATASETS.items():
                if ds["inputs"].resolve() == resolved:
                    return key
        except Exception:
            pass
    return None


# -- runs --------------------------------------------------------------------------------------

def read_events(path: Path) -> list[dict]:
    events = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass  # a line still being written
    except FileNotFoundError:
        pass
    return events


def case_summary(case_dir: Path) -> dict:
    path = case_dir / "events.jsonl"
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"name": case_dir.name, "status": "pending", "steps": 0}
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _summary_cache.get(str(path))
    if cached and cached[0] == key:
        return cached[1]
    summary = {"name": case_dir.name, "status": "running", "steps": 0, "errors": 0, "started_at": None,
               "ended_at": None, "current": None, "output": None, "stages": []}
    open_steps: dict[str, str] = {}
    step_names: dict[str, str] = {}
    stages: dict[str, dict] = {}  # step id -> the solid saved after that step
    for event in read_events(path):
        kind = event.get("t")
        if kind == "case_start":
            summary["started_at"] = event.get("ts")
        elif kind == "case_end":
            summary["status"] = event.get("status", "ok")
            summary["ended_at"] = event.get("ts")
        elif kind == "step_start":
            summary["steps"] += 1
            open_steps[event.get("id")] = event.get("name")
            step_names[event.get("id")] = event.get("name")
        elif kind == "step_end":
            open_steps.pop(event.get("id"), None)
            if event.get("status") == "error":
                summary["errors"] += 1
            if event.get("id") in stages:
                stages[event.get("id")]["volume_cm3"] = (event.get("metrics") or {}).get("volume_cm3")
        elif kind == "artifact" and event.get("role") == "output":
            summary["output"] = event.get("path")
        elif kind == "artifact" and event.get("role") == "stage":
            stages[event.get("step")] = {"step": step_names.get(event.get("step"), event.get("name")),
                                         "path": event.get("path")}
        if summary["started_at"] is None:
            summary["started_at"] = event.get("ts")
        summary["last_ts"] = event.get("ts")
    summary["stages"] = list(stages.values())
    if open_steps and summary["status"] == "running":
        summary["current"] = list(open_steps.values())[-1]
    _summary_cache[str(path)] = (key, summary)
    return summary


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        # On Windows os.kill(pid, 0) is not a probe: it calls TerminateProcess with exit code 0,
        # which silently killed every run the dashboard had not started itself
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def recently_written(run_dir: Path, seconds: float = 120) -> bool:
    try:
        return any(time.time() - p.stat().st_mtime < seconds for p in run_dir.glob("*/events.jsonl"))
    except OSError:
        return False


def run_summary(run_dir: Path) -> dict | None:
    try:
        info = json.loads((run_dir / "run.json").read_text())
    except Exception:
        return None
    info["id"] = run_dir.name
    info["dataset"] = dataset_of(info)
    proc = PROCS.get(run_dir.name)
    if info.get("status") == "running":
        if proc is not None:
            code = proc.poll()
            if code is not None:
                info["status"] = "stopped" if code < 0 else "crashed"
                info["exit_code"] = code
        elif not pid_alive(info.get("pid")) and not recently_written(run_dir):
            info["status"] = "crashed"   # a run in a container has a pid this machine does not know: its writes tell
    cases = [case_summary(d) for d in run_dir.iterdir() if d.is_dir() and (d / "events.jsonl").is_file()]
    cases.sort(key=lambda c: case_number(c["name"]))
    if info["status"] != "running":
        # the process died mid-case
        cases = [dict(c, status="error") if c["status"] == "running" else c for c in cases]
        if not info.get("ended_at"):
            info["ended_at"] = max((c.get("last_ts") or 0 for c in cases), default=None) or info.get("started_at")
    info["cases"] = cases
    info["has_log"] = (run_dir / "stdout.log").is_file()
    return info


def list_runs() -> list[dict]:
    runs = [summary for d in RUNS.iterdir() if d.is_dir() and (summary := run_summary(d))] if RUNS.is_dir() else []
    linked = set()
    for run in agent_runs(runs):
        if run.get("linked"):
            linked.add(run["linked"])
        runs.append(run)
    # a traced run is shown inside the agent run that continued it; one that died before any case is left out
    runs = [r for r in runs if r["id"] not in linked and (r["cases"] or r["status"] == "running")]
    return sorted(runs, key=lambda r: r.get("started_at") or 0, reverse=True)


# -- agent runs (ui/agents.py): listed and read like traced runs ------------------------------------

_facts_index: dict[str, dict[bytes, str]] = {}


def case_dataset(unit: Path, case: str) -> str | None:
    """The set a case of an agent run came from: the one whose facts.json the agent was given."""
    try:
        given = (unit / "ws" / "facts.json").read_bytes()
    except OSError:
        return None
    for key, ds in DATASETS.items():
        try:
            if (ds["inputs"] / case / "facts.json").read_bytes() == given:
                return key
        except OSError:
            continue
    return None


def true_volume(dataset: str | None, case: str) -> float | None:
    try:
        return float(json.loads((DATASETS[dataset]["inputs"] / case / "facts.json").read_text())["volume_mm3"])
    except Exception:
        return None


def agent_runs(traced: list[dict]) -> list[dict]:
    result, taken = [], set()   # a traced run continues in one agent run at most
    for name, folder in agents.run_dirs().items():
        units = agents.units(folder)
        if not units:
            continue
        now = time.time()
        cases, datasets, first = [], set(), None
        for unit in units:
            if unit.parent != folder:   # several agents on one case: only the first is shown here
                if any(c["name"] == unit.parent.name for c in cases):
                    continue
            a = agents.unit_summary(folder, unit, now)
            t0 = agents.started_at(unit)
            first = min(first or t0 or now, t0 or now)
            dataset = case_dataset(unit, a["case"])
            datasets.add(dataset)
            live = a["state"] in agents.LIVE or a["state"] == "waiting"
            cases.append({"name": a["case"], "unit": unit.relative_to(folder).as_posix(), "dataset": dataset,
                          "status": "running" if live else "error" if a["state"] == "failed" else "stopped" if a["state"] == "stopped" else "ok",
                          "current": ("agent: " + (a["doing"]["text"] if a.get("doing") else a["state"])) if live else None,
                          "started_at": t0, "ended_at": a["ended"], "last_ts": a["ended"] or now,
                          "score": a["published"] if a["published"] is not None else a["base"],
                          "by": ("agent" if a["source"] == "agent" else "pipeline") if a["published"] is not None
                          else "pipeline" if (unit / "ws" / "base.step").is_file() else "box",
                          "hollow": "void" in (a.get("source") or ""), "stages": []})
        cases.sort(key=lambda c: case_number(c["name"]))
        live = any(c["status"] == "running" for c in cases)
        # the traced run that started the agents: named in the work dir (scripts/main.py), else the only traced run
        # of the same cases that began up to 15 minutes before them
        named = (folder / "trace_run").read_text().strip() if (folder / "trace_run").is_file() else None
        if named:
            linked = next((r for r in traced if r["id"] == Path(named).name), None)
        else:   # main.py names both folders by the clock at the start of the run, a moment apart
            def stamp(name: str) -> float | None:
                try:
                    return datetime.strptime(name[:15], "%Y%m%d-%H%M%S").timestamp()
                except ValueError:
                    return None
            ours = stamp(folder.name)
            near = [r for r in traced if ours is not None and stamp(r["id"]) is not None and 0 <= ours - stamp(r["id"]) <= 1
                    and r["id"] not in taken and {c["name"] for c in r["cases"]} >= {c["name"] for c in cases}]
            linked = min(near, key=lambda r: ours - stamp(r["id"])) if near else None
        if linked:
            taken.add(linked["id"])
        known = [d for d in datasets if d]
        result.append({"id": agents.PREFIX + name, "agents": True, "status": "running" if live else "ok",
                       "started_at": linked["started_at"] if linked else first,
                       "ended_at": None if live else max((c["last_ts"] or 0 for c in cases), default=first),
                       "dataset": known[0] if len(set(known)) == 1 else (linked or {}).get("dataset"),
                       "linked": linked["id"] if linked else None, "cases": cases, "has_log": False,
                       "launched_by": "agents",
                       # stoppable from the dashboard: the container forge_run.sh started for this run
                       "container": agents.container_of(folder) if live and agents.container_of(folder) in (agents.containers() or set()) else None,
                       "stopped": any(c["status"] == "stopped" for c in cases)})
    return result


def agent_unit(run_id: str, case: str) -> tuple[Path, dict] | None:
    folder = agents.run_dirs().get(run_id[len(agents.PREFIX):])
    run = next((r for r in agent_runs([]) if r["id"] == run_id), None) if folder else None
    entry = next((c for c in (run or {}).get("cases", []) if c["name"] == case), None)
    return (folder / entry["unit"], run) if entry else None


def agent_events(run_id: str, case: str) -> list[dict]:
    found = agent_unit(run_id, case)
    if not found:
        return []
    unit, _ = found
    run = next((r for r in list_runs() if r["id"] == run_id), None) or {}
    pipeline = None
    if run.get("linked"):
        pipeline = read_events(RUNS / run["linked"] / case / "events.jsonl")
        # the traced run's files, reached from this run's folder
        for event in pipeline:
            if event.get("path") and event.get("t") == "artifact":
                event["path"] = f"../../{run['linked']}/{case}/{event['path']}"
    dataset = next((c["dataset"] for c in run.get("cases", []) if c["name"] == case), None) or run.get("dataset")
    return agents.case_events(unit, true_volume(dataset, case), pipeline)


def load_dotenv() -> dict[str, str]:
    values = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                if value.strip():
                    values[key.strip()] = value.strip()
    return values


def start_run(only: list[str], target: str, dataset: str = "practice") -> dict:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-ui"
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    ds = DATASETS[dataset]
    command = [sys.executable, "scripts/main.py"]
    if dataset != "practice":
        command += ["--input_dir", str(ds["inputs"])]
    if target != "artifacts" or not ds["outputs"]:  # keep experiments out of the committed outputs until promoted
        command += ["--output_dir", str(run_dir / "outputs")]
    if only:
        command += ["--only", *only]
    env = {**load_dotenv(), **os.environ, "TRACE_RUN_ID": run_id, "TRACE_DIR": str(RUNS), "TRACE": "1",
           "PYTHONUNBUFFERED": "1"}
    log = open(run_dir / "stdout.log", "wb")
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    PROCS[run_id] = proc
    (run_dir / "run.json").write_text(json.dumps({
        "id": run_id, "status": "running", "started_at": time.time(), "pid": proc.pid, "launched_by": "ui",
        "argv": command[1:], "only": only, "target": target, "dataset": dataset,
    }))
    return {"id": run_id}


def promote(run_id: str, only: list[str] | None) -> dict:
    """Copy a run's output .stp files into artifacts/outputs (the files that get committed and scored)."""
    run_dir = RUNS / run_id
    copied = []
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    for case_dir in sorted(run_dir.iterdir()):
        summary = case_summary(case_dir) if case_dir.is_dir() else None
        if not summary or not summary.get("output") or (only and case_dir.name not in only):
            continue
        shutil.copyfile(case_dir / summary["output"], OUTPUTS / f"{case_dir.name}.stp")
        copied.append(case_dir.name)
    return {"copied": copied}


# -- pytest ------------------------------------------------------------------------------------

def run_pytest() -> dict:
    with _pytest_lock, tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        started = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}"],
                cwd=ROOT, capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "tests": [], "stdout": "pytest timed out after 300 s", "duration": 300}
        tests = []
        if report.is_file():
            for node in ET.parse(report).getroot().iter("testcase"):
                status, message = "passed", ""
                for child in node:
                    if child.tag in ("failure", "error"):
                        status, message = "failed", (child.get("message") or "") + "\n" + (child.text or "")
                    elif child.tag == "skipped":
                        status, message = "skipped", child.get("message") or ""
                name = node.get("name", "")
                param = re.search(r"\[(.+)\]$", name)
                tests.append({
                    "name": name.split("[")[0], "param": param.group(1) if param else None,
                    "file": node.get("classname", ""), "status": status, "message": message.strip(),
                    "time": float(node.get("time") or 0),
                })
        return {"ok": proc.returncode == 0, "exit_code": proc.returncode, "tests": tests,
                "stdout": (proc.stdout + proc.stderr)[-20000:], "duration": time.time() - started,
                "ran_at": time.time()}


# -- http --------------------------------------------------------------------------------------

_sources_guard = threading.Lock()
_sources_locks: dict[str, threading.Lock] = {}   # one per case (and per view drawn alone): cases do not hold each other up
_sources_slots = threading.Semaphore(2)          # ... but at most two whole cases are drawn at a time
_sources_failed: dict[str, str] = {}             # what could not be drawn and why, until the code changes
SOURCE_MODULES = ("sources", "drawing", "tone", "regions", "lines", "verify", "visual_hull")
SKETCHES = ("lines", "faces")                    # the pictures a view gives alone, in about a second


# argv: src, the case's inputs, the folder to write to, the tone table's name, and optionally one
# view: then only its lines and faces are drawn. A file appears under its name only when complete.
MAKE_SOURCES = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agentic_cad import sources, tone, verify
from agentic_cad import visual_hull as vh
case_dir, folder, table_name = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
only = sys.argv[5] if len(sys.argv) > 5 else None
dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
observed = verify.observe(case_dir, dims)
if only:
    made = {(only, what): png for what, png in sources.sketches(observed[only]).items()}
else:
    tables = tone.tables()
    made = sources.pictures(observed, dims, tables.get(table_name) or tables.get(tone.DEFAULT))
for (name, what), png in made.items():
    part = folder / f".{name}_{what}.{os.getpid()}.part"
    part.write_bytes(png)
    part.replace(folder / f"{name}_{what}.png")
(folder / (f"{only}.done" if only else "done")).write_text("")
"""


class SourceError(Exception):
    pass


def _draw_sources(folder: Path, dataset: str, case: str, view: str | None) -> None:
    """Draws the pictures of a case into `folder` in a process of its own (so that a change to the
    code shows without restarting the dashboard): all of them, or the lines and faces of one view."""
    key = f"{folder}|{view or ''}"
    with _sources_guard:
        lock = _sources_locks.setdefault(key, threading.Lock())
    with lock:
        marker = folder / (f"{view}.done" if view else "done")
        if marker.exists() or (folder / "done").exists():
            return
        if key in _sources_failed:
            raise SourceError(_sources_failed[key])
        folder.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-c", MAKE_SOURCES, str(ROOT / "src"), str(DATASETS[dataset]["inputs"] / case),
                   str(folder), "eval" if dataset == "eval" else "organisers", *([view] if view else [])]
        try:
            if view:
                proc = subprocess.run(command, env={**os.environ, "TRACE": "0"}, timeout=120, capture_output=True, text=True)
            else:
                with _sources_slots:
                    proc = subprocess.run(command, env={**os.environ, "TRACE": "0"}, timeout=300, capture_output=True, text=True)
            said = (proc.stderr or proc.stdout or "").strip().splitlines()
            reason = None if proc.returncode == 0 else (said[-1] if said else f"exit code {proc.returncode}")
        except subprocess.TimeoutExpired:
            reason = "drawing the views took too long"
        if reason or not marker.exists():
            _sources_failed[key] = reason or "the pictures were not written"
            raise SourceError(_sources_failed[key])


def source_picture(dataset: str, case: str, view: str, kind: str) -> Path | None:
    """One derived source of one view of a case (src/agentic_cad/sources.py) as a PNG, kept under
    runs/_sources_cache until one of SOURCE_MODULES changes; the pictures of older code are then
    deleted. The lines and faces of a view are drawn alone, in about a second; the normals and
    depths need every view of the case (5 to 15 s), and all its pictures are drawn with them.
    Raises SourceError, with the last line the process wrote, if they cannot be drawn."""
    source_dir = ROOT / "src" / "agentic_cad"
    stamp = str(int(max((source_dir / f"{name}.py").stat().st_mtime for name in SOURCE_MODULES)))
    cache = RUNS / "_sources_cache"
    folder = cache / stamp / dataset / case
    wanted = folder / f"{view}_{kind}.png"
    if wanted.exists():
        return wanted
    if not (DATASETS[dataset]["inputs"] / case / "images").is_dir():
        return None
    for old in cache.iterdir() if cache.is_dir() else []:
        if old.is_dir() and old.name != stamp:
            shutil.rmtree(old, ignore_errors=True)
    _draw_sources(folder, dataset, case, view if kind in SKETCHES else None)
    return wanted if wanted.exists() else None


# -- marks: places on a solid the user pointed at in the dashboard, for tools/marks.py --------------

MARKS = RUNS / "_marks"


def url_file(url) -> str | None:
    """The file behind a /files/ address, from the repository's root."""
    if not isinstance(url, str) or not url.startswith("/files/"):
        return None
    root, _, rest = unquote(urlparse(url).path)[len("/files/"):].partition("/")
    path = safe_join(FILE_ROOTS[root], rest) if root in FILE_ROOTS else None
    return os.path.relpath(path, ROOT) if path else None


def save_marks(body: dict) -> dict:
    name, dataset, case = (str(body.get(key) or "") for key in ("id", "dataset", "case"))
    if not NAME_RE.match(name) or dataset not in DATASETS or not NAME_RE.match(case):
        raise ValueError("bad name")
    folder = MARKS / name
    folder.mkdir(parents=True, exist_ok=True)
    shots = []
    for tag, data in (body.get("shots") or {}).items():
        if tag in ("ours", "truth") and isinstance(data, str) and data.startswith("data:image/png;base64,"):
            (folder / f"shot_{tag}.png").write_bytes(base64.b64decode(data.split(",", 1)[1]))
            shots.append(f"shot_{tag}.png")
    marks = [m for m in body.get("marks") or [] if isinstance(m, dict)]
    for mark in marks:
        mark["solid_file"] = url_file(mark.get("solid"))
    record = {"id": name, "saved_at": datetime.now().isoformat(timespec="seconds"), "dataset": dataset, "case": case,
              "run": body.get("run"), "inputs": os.path.relpath(DATASETS[dataset]["inputs"] / case, ROOT),
              "truth_file": url_file(body.get("truth")), "shots": shots, "marks": marks}
    (folder / "marks.json").write_text(json.dumps(record, indent=1))
    (MARKS / "latest").write_text(name)
    return {"id": name, "path": os.path.relpath(folder, ROOT)}


def safe_join(base: Path, relative: str) -> Path | None:
    target = (base / relative).resolve()
    return target if target.is_relative_to(base.resolve()) and target.is_file() else None


def list_tree(base: Path, url_prefix: str) -> list[dict]:
    if not base.is_dir():
        return []
    return [
        {"path": str(p.relative_to(base)), "url": f"{url_prefix}/{p.relative_to(base).as_posix()}", **file_info(p)}
        for p in sorted(base.rglob("*")) if p.is_file() and p.name != ".DS_Store"
    ]


class Handler(BaseHTTPRequestHandler):
    server_version = "cad-dashboard"

    def log_message(self, *args):  # keep the terminal quiet
        pass

    def send_json(self, data, status: int = 200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path | None, cache: bool = False):
        if path is None:
            return self.send_json({"error": "not found"}, 404)
        content_type = mimetypes.guess_type(path.name)[0] or (
            "text/plain" if path.suffix in (".stp", ".step", ".jsonl", ".log", ".md", ".py") else
            "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        query = parse_qs(url.query)
        try:
            if path == "/":
                return self.send_file(STATIC / "index.html")
            if path.startswith("/static/"):
                return self.send_file(safe_join(STATIC, path[len("/static/"):]))
            if path.startswith("/files/"):
                root, _, rest = path[len("/files/"):].partition("/")
                if root == "agents":   # a file of an agent's work directory
                    run, _, rest = rest.partition("/")
                    return self.send_file(agents.file(run, rest))
                if root == "runs" and rest.startswith(agents.PREFIX):   # a file of an agent run
                    run, _, rest = rest.partition("/")
                    case, _, rest = rest.partition("/")
                    found = agent_unit(run, case) if NAME_RE.match(case) else None
                    return self.send_file(agents.file(run[len(agents.PREFIX):], f"{found[0].relative_to(agents.run_dirs()[run[len(agents.PREFIX):]]).as_posix()}/{rest}")
                                          if found else None)
                if root not in FILE_ROOTS:
                    return self.send_json({"error": "not found"}, 404)
                return self.send_file(safe_join(FILE_ROOTS[root], rest),
                                      cache="/inputs/" in path or "/ground_truth/" in path)
            if path == "/api/agents":
                return self.send_json(agents.list_runs())
            if path == "/api/agent":
                found = agents.detail(query["run"][0], query["agent"][0])
                return self.send_json(found if found else {"error": "not found"}, 200 if found else 404)
            if path == "/api/state":
                return self.send_json({"datasets": list_datasets(), "runs": list_runs(), "root": str(ROOT),
                                       "now": time.time()})
            if path == "/api/cases":
                return self.send_json({"cases": list_cases(query["dataset"][0])})
            if path == "/api/description":
                ds = DATASETS[query.get("dataset", ["practice"])[0]]
                return self.send_file(safe_join(ds["inputs"], f"{query['case'][0]}/description.md"))
            if path == "/api/source":
                dataset, case, view, kind = (query[key][0] for key in ("dataset", "case", "view", "kind"))
                if dataset not in DATASETS or not all(NAME_RE.match(value) for value in (case, view, kind)):
                    return self.send_json({"error": "bad name"}, 400)
                try:
                    return self.send_file(source_picture(dataset, case, re.sub(r"^view_\d+_", "", view), kind))
                except SourceError as error:
                    return self.send_json({"error": str(error)}, 500)
            if path == "/api/events":
                run, case = query["run"][0], query["case"][0]
                if not (NAME_RE.match(run) and NAME_RE.match(case)):
                    return self.send_json({"error": "bad name"}, 400)
                if run.startswith(agents.PREFIX):   # made up anew on every request: stops can come in between
                    events = agent_events(run, case)
                    return self.send_json({"events": events, "total": len(events), "replace": True})
                events = read_events(RUNS / run / case / "events.jsonl")
                after = int(query.get("after", ["0"])[0])
                return self.send_json({"events": events[after:], "total": len(events)})
            if path == "/api/log":
                run = query["run"][0]
                log = safe_join(RUNS, f"{run}/stdout.log") if NAME_RE.match(run) else None
                return self.send_json({"text": log.read_text(errors="replace")[-200_000:] if log else ""})
            if path == "/api/files":
                case = query["case"][0]
                if not NAME_RE.match(case):
                    return self.send_json({"error": "bad name"}, 400)
                ds = DATASETS[query.get("dataset", ["practice"])[0]]
                groups = [{"label": "Inputs", "files": list_tree(ds["inputs"] / case, f"{ds['inputs_url']}/{case}")}]
                if ds["truth"]:
                    groups.append({"label": "Expected output (ground truth)", "files": [
                        f for f in list_tree(ds["truth"], ds["truth_url"]) if f["path"] == f"{case}.stp"]})
                if ds["outputs"]:
                    groups.append({"label": "Committed output", "files": [
                        f for f in list_tree(ds["outputs"], "/files/artifacts/outputs") if f["path"] == f"{case}.stp"]})
                run = query.get("run", [""])[0]
                if run and NAME_RE.match(run):
                    groups.append({"label": f"Run {run}", "files": list_tree(RUNS / run / case,
                                                                              f"/files/runs/{run}/{case}")})
                return self.send_json({"groups": groups})
            return self.send_json({"error": "not found"}, 404)
        except (KeyError, IndexError, ValueError):
            return self.send_json({"error": "bad request"}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        # Only the dashboard itself may trigger actions: same-origin JSON requests from localhost
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if (origin and urlparse(origin).netloc != host) or "application/json" not in self.headers.get(
                "Content-Type", ""):
            return self.send_json({"error": "forbidden"}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            only = [c for c in body.get("only") or [] if NAME_RE.match(c)]
            run = body.get("run", "")
            if run and not NAME_RE.match(run):
                return self.send_json({"error": "bad name"}, 400)
            path = urlparse(self.path).path
            if path == "/api/run":
                dataset = body.get("dataset", "practice")
                if dataset not in DATASETS:
                    return self.send_json({"error": "unknown dataset"}, 400)
                return self.send_json(start_run(only, body.get("target", "run"), dataset))
            if path == "/api/stop":
                if run.startswith(agents.PREFIX):   # an agent run: its container
                    folder = agents.run_dirs().get(run[len(agents.PREFIX):])
                    return self.send_json({"ok": True, "result": agents.stop_run(folder)} if folder else {"error": "no such run"})
                proc = PROCS.get(run)
                if proc and proc.poll() is None:
                    proc.terminate()
                return self.send_json({"ok": True})
            if path == "/api/promote":
                return self.send_json(promote(run, only or None))
            if path == "/api/marks":
                return self.send_json(save_marks(body))
            if path == "/api/pytest":
                return self.send_json(run_pytest())
            return self.send_json({"error": "not found"}, 404)
        except Exception as error:  # surface the reason in the UI instead of dropping the connection
            return self.send_json({"error": f"{type(error).__name__}: {error}"}, 500)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"dashboard: http://localhost:{args.port}   (traces: {RUNS})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for proc in PROCS.values():
            if proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    main()
