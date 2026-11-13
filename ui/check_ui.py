"""Checks the dashboard in a real (headless) Chrome: what a poll redraws must not move the reader.

    uv run python ui/check_ui.py          # exit 0 when every check passes

Makes a fake agent run with 12 agents at work in a temporary folder, starts ui/server.py on it, opens the page
in Chrome with remote debugging and runs ui/check_scroll.mjs (Node 22 or later). It fails if a poll's redraw moves
the running list or the case list, if the side list is unstyled, or if an agent run lacks its pipeline and agent
bands and its live stop. Needs Google Chrome and node.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

UI = Path(__file__).resolve().parent
CHROME = next((p for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", shutil.which("google-chrome") or "",
                           shutil.which("chromium") or "") if p and Path(p).exists()), None)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_run(root: Path) -> None:
    """12 agents at work: a log that says so, one checked candidate each, a few steps of their own."""
    now = time.time()
    for n in range(1, 13):
        case = root / "live" / f"test_case_{n}"
        cand = case / "ws" / ".cad" / "cand_000"
        cand.mkdir(parents=True)
        (case / "ws" / ".cad" / "meta.json").write_text(json.dumps({"case_dir": f"/x/test_case_{n}", "deadline": 0}))
        (cand / "result.json").write_text(json.dumps({"index": 0, "score": 0.74, "measurement": {"terms": {}}}))
        (case / "log.jsonl").write_text(json.dumps({"event": "base", "measurement": {"score": 0.7}, "t": now - 100}) + "\n"
                                        + json.dumps({"event": "agent_started", "pid": 1, "t": now - 90}) + "\n")
        if n == 1:   # a script saved after the check: its preview solid is a draft stop
            (case / "previews").mkdir()
            (case / "previews.jsonl").write_text(json.dumps({"index": 0, "t": now - 5, "script": "part.py", "ok": True,
                                                             "path": str(case / "previews" / "000.stp"), "sha": "x"}) + "\n")
        (case / "agent_0.jsonl").write_text("\n".join(json.dumps(e) for e in (
            {"type": "text", "text": "Looking at the views."},
            {"type": "tool_call", "callId": "1", "tool": "bash", "input": {"command": "./check part.py"}},
            {"type": "tool_result", "callId": "1", "status": "completed", "result": "Local score 0.740"})) + "\n")


def fake_finished_agent(root: Path) -> None:
    """An agent that checked one solid and published it, after which the pipeline's solid replaced it in the file."""
    now = time.time()
    case = root / "done" / "test_case_3"
    cand = case / "ws" / ".cad" / "cand_000"
    cand.mkdir(parents=True)
    (case / "ws" / ".cad" / "meta.json").write_text(json.dumps({"case_dir": "/x/test_case_3", "deadline": 0}))
    (cand / "result.json").write_text(json.dumps({"index": 0, "score": 0.96, "measurement": {"terms": {}}}))
    os.utime(cand / "result.json", (now - 200, now - 200))   # a check's time is when its result was written
    # the agent is given the case's facts.json: that is how the dashboard tells which set the case is from
    shutil.copyfile(UI.parent / "artifacts" / "inputs" / "test_case_3" / "facts.json", case / "ws" / "facts.json")
    (case / "pipeline.stp").write_text("")
    rows = [{"event": "base", "measurement": {"score": 0.47}, "t": now - 300},
            {"event": "agent_started", "pid": 1, "t": now - 290},
            {"event": "published", "check": 0, "score": 0.96, "source": "agent", "t": now - 200},
            {"event": "pipeline", "measurement": {"score": 0.86}, "t": now - 100},
            {"event": "published", "check": None, "score": 0.86, "source": "pipeline", "t": now - 100},
            {"event": "done", "t": now - 50}]
    (case / "log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (case / "agent_0.jsonl").write_text("\n".join(json.dumps(e) for e in (
        {"type": "tool_call", "callId": "1", "tool": "bash", "input": {"command": "./check part.py"}},
        {"type": "tool_result", "callId": "1", "status": "completed", "result": "Local score 0.960"})) + "\n")
    os.utime(case / "log.jsonl", (now - 50, now - 50))


def fake_traced_run(runs: Path) -> None:
    """A finished pipeline run of practice 2 whose last step, export, has the output file but no solid of its own."""
    now = time.time()
    folder = runs / "20260101-000000-test" / "test_case_2"
    folder.mkdir(parents=True)
    (folder.parent / "run.json").write_text(json.dumps({"status": "ok", "started_at": now - 60, "ended_at": now - 10,
                                                        "dataset": "practice", "pid": 0, "meta": {}}))
    rows = [{"t": "case_start", "ts": now - 60}]
    for i, name in enumerate(("bounding_box", "cut_front_back", "split_faces", "export_step")):
        rows.append({"t": "step_start", "id": f"s{i}", "name": name, "ts": now - 50 + i})
        if name != "export_step":
            rows.append({"t": "artifact", "step": f"s{i}", "name": f"{name}.stp", "path": f"files/{name}.stp", "role": "stage",
                         "ts": now - 50 + i})
        rows.append({"t": "step_end", "id": f"s{i}", "status": "ok", "ts": now - 49 + i})
    rows += [{"t": "artifact", "step": None, "name": "test_case_2.stp", "path": "files/out.stp", "role": "output", "ts": now - 11},
             {"t": "case_end", "ts": now - 10, "status": "ok"}]
    (folder / "events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def main() -> int:
    if not CHROME or not shutil.which("node"):
        print("needs Google Chrome and node")
        return 2
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        fake_run(tmp / "agents")
        fake_finished_agent(tmp / "agents")
        fake_traced_run(tmp / "runs")
        port, debug = free_port(), free_port()
        server = subprocess.Popen([sys.executable, str(UI / "server.py"), "--port", str(port)],
                                  env={**os.environ, "AGENT_DIRS": str(tmp / "agents"), "TRACE_DIR": str(tmp / "runs")},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chrome = None
        try:
            time.sleep(2)
            chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={debug}",
                                       f"--user-data-dir={tmp / 'chrome'}", "--window-size=1600,900",
                                       f"http://localhost:{port}/#/practice/test_case_1"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3)
            done = subprocess.run(["node", str(UI / "check_scroll.mjs"), str(debug),
                                   str(tmp / "agents" / "live" / "test_case_1")], timeout=90)
            print("all checks passed" if done.returncode == 0 else "FAILED (see above)")
            return done.returncode
        finally:
            for process in (chrome, server):
                if process:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()


if __name__ == "__main__":
    sys.exit(main())
