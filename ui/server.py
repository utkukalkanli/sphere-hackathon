"""Local dashboard for the Prompt-to-CAD pipeline. Standard library only.

    uv run python ui/server.py            # http://localhost:8765

Shows the test cases of the practice set (artifacts/inputs) and the local eval set (datasets/eval:
brief, views, facts, expected .stp), the traces written by agentic_cad.trace under runs/, and the
output .stp files next to what was expected. It can also launch scripts/main.py and pytest. Development tool only:
it is not part of the submitted image (ui/ is in .dockerignore) and listens on localhost.
"""

from __future__ import annotations

import argparse
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
    return sorted(cases, key=lambda c: c["number"])


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
               "ended_at": None, "current": None, "output": None}
    open_steps: dict[str, str] = {}
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
        elif kind == "step_end":
            open_steps.pop(event.get("id"), None)
            if event.get("status") == "error":
                summary["errors"] += 1
        elif kind == "artifact" and event.get("role") == "output":
            summary["output"] = event.get("path")
        if summary["started_at"] is None:
            summary["started_at"] = event.get("ts")
        summary["last_ts"] = event.get("ts")
    if open_steps and summary["status"] == "running":
        summary["current"] = list(open_steps.values())[-1]
    _summary_cache[str(path)] = (key, summary)
    return summary


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
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
        elif not pid_alive(info.get("pid")):
            info["status"] = "crashed"
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
    if not RUNS.is_dir():
        return []
    runs = [summary for d in RUNS.iterdir() if d.is_dir() and (summary := run_summary(d))]
    return sorted(runs, key=lambda r: r.get("started_at") or 0, reverse=True)


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
                if root not in FILE_ROOTS:
                    return self.send_json({"error": "not found"}, 404)
                return self.send_file(safe_join(FILE_ROOTS[root], rest),
                                      cache="/inputs/" in path or "/ground_truth/" in path)
            if path == "/api/state":
                return self.send_json({"datasets": list_datasets(), "runs": list_runs(), "root": str(ROOT),
                                       "now": time.time()})
            if path == "/api/cases":
                return self.send_json({"cases": list_cases(query["dataset"][0])})
            if path == "/api/description":
                ds = DATASETS[query.get("dataset", ["practice"])[0]]
                return self.send_file(safe_join(ds["inputs"], f"{query['case'][0]}/description.md"))
            if path == "/api/events":
                run, case = query["run"][0], query["case"][0]
                if not (NAME_RE.match(run) and NAME_RE.match(case)):
                    return self.send_json({"error": "bad name"}, 400)
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
                proc = PROCS.get(run)
                if proc and proc.poll() is None:
                    proc.terminate()
                return self.send_json({"ok": True})
            if path == "/api/promote":
                return self.send_json(promote(run, only or None))
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
