"""Tests for the LangGraph engine that need no model endpoint."""

import time
from pathlib import Path

from langchain_core.messages import AIMessage

from agentic_cad import graph
from agentic_cad.agent import Attempt
from agentic_cad.config import Settings
from agentic_cad.evaluate import Evaluation
from agentic_cad.trace import Tracer

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_3 = REPO_ROOT / "artifacts/inputs/test_case_3"


class FakeStep:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))


def test_normalise_ignores_comments_and_blank_lines():
    a = "x = 1  # width\n\nresult = box(0, 0, 0, x, 2, 3)\n"
    b = "x = 1\nresult = box(0, 0, 0, x, 2, 3)   # the part\n\n"
    assert graph._normalise(a) == graph._normalise(b)
    assert graph._normalise(a) != graph._normalise("x = 2\nresult = box(0, 0, 0, x, 2, 3)\n")


def test_repair_turns_a_python_block_into_a_build_call():
    step = FakeStep()
    reply = AIMessage(content="Here is the part:\n```python\nresult = box(0, 0, 0, 1, 2, 3)\n```")
    fixed = graph._repair(reply, step)
    assert fixed.tool_calls and fixed.tool_calls[0]["name"] == "build_and_evaluate"
    assert fixed.tool_calls[0]["args"]["code"] == "result = box(0, 0, 0, 1, 2, 3)\n"
    assert any("converted" in msg for _, msg in step.logs)
    # a reply that already calls a tool is left alone; one with neither is returned unchanged
    call = AIMessage(content="", tool_calls=[{"name": "submit", "args": {"reason": "done"}, "id": "x", "type": "tool_call"}])
    assert graph._repair(call, step) is call
    plain = AIMessage(content="I need to think about the flange first.")
    assert graph._repair(plain, step) is plain


def test_history_line_summarises_an_attempt():
    ev = Evaluation(score=0.7312, volume_err=0.123, iou_min=0.81, missing_diameters=["d=6.75 along Y"])
    attempt = Attempt(3, "result = None", {"ok": True, "bbox": [19.0, 14.0, 50.8]}, ev, None)
    line = graph._history_line(attempt, "added the rib")
    assert line.startswith("#3 score 0.731") and "vol +12%" in line and "iou min 0.81" in line
    assert "1 diameters missing" in line and line.endswith("| change: added the rib")
    failed = Attempt(4, "x", {"ok": False, "error": "NameError: cyl"}, Evaluation(), None)
    assert graph._history_line(failed, "").startswith("#4 FAILED (NameError: cyl)")


def test_run_case_writes_the_fallback_block_when_the_llm_is_down(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9")   # nothing listens here
    monkeypatch.setenv("OPENAI_MODEL", "dummy")
    monkeypatch.setenv("VLM_BASE_URL", "")
    monkeypatch.setenv("AGENT_MAX_STEPS", "2")
    monkeypatch.delenv("TRACE", raising=False)
    output = tmp_path / "test_case_3.stp"
    summary = graph.run_case(Settings.from_env(), "test_case_3", CASE_3, (CASE_3 / "description.md").read_text(),
                             sorted((CASE_3 / "images").glob("view_*.png")), None, output, time.time() + 120,
                             Tracer().case("test_case_3"))
    assert output.is_file() and summary["written"]
    assert summary["status"] == "error" and summary["attempt"] == 0 and summary["builds"] == 0


def test_rank_prefers_silhouette_match_over_composite_score():
    # volume right but shape wrong (what the model tends to produce) must not beat a plain block that keeps
    # the silhouettes: the published attempt follows the silhouette match
    block = Evaluation(score=0.53, volume_err=0.58, iou_mean=0.87)
    wrong_shape = Evaluation(score=0.65, volume_err=0.02, iou_mean=0.74)
    assert block.rank > wrong_shape.rank
    assert Evaluation(score=0.4).rank == 0.4   # no views compared: fall back to the score
