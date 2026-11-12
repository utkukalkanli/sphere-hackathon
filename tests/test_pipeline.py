"""Tests for the agent loop. The LLM is replaced by a scripted client; no endpoint is needed."""

import json
from pathlib import Path

import pytest

from agentic_cad import prompts
from agentic_cad.config import Settings
from agentic_cad.evaluate import evaluate, facts_from_description
from agentic_cad.trace import Tracer

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_3 = REPO_ROOT / "artifacts/inputs/test_case_3"   # flanged sleeve: bore 76.2, OD 127, flange 132.08

SLEEVE_NO_BORE = """Analysis: a sleeve.
```python
body = cyl('x', 127.0, 0, 19.05, 66.04, 66.04).union(cyl('x', 132.08, 19.05, 21.59, 66.04, 66.04))
result = body
```"""
SLEEVE = """<think>forgot the bore</think>I add the through bore.
```python
body = cyl('x', 127.0, 0, 19.05, 66.04, 66.04).union(cyl('x', 132.08, 19.05, 21.59, 66.04, 66.04))
result = body.cut(cyl('x', 76.2, -1, 23, 66.04, 66.04))
```"""


class ScriptedClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, messages, timeout, temperature=None):
        self.calls.append(messages)
        return self.replies[min(len(self.calls), len(self.replies)) - 1], {"prompt_tokens": 1, "completion_tokens": 1}


def test_extract_code_takes_last_block_after_thinking():
    reply = "<think>```python\nresult = None\n```</think>plan\n```python\nresult = box(0,0,0,1,1,1)\n```\nbye"
    assert prompts.extract_code(reply) == "result = box(0,0,0,1,1,1)\n"
    assert prompts.extract_code("no code here") is None
    assert prompts.extract_code("```python\nresult = box(0,0,0,1,1,1)\n") == "result = box(0,0,0,1,1,1)\n"


def test_facts_from_description():
    facts = facts_from_description("**Envelope:** 19.0 x 19.0 x 50.8 mm (X x Y x Z); volume 9.20 cm^3; 2 holes")
    assert facts["bounding_box_mm"] == {"x": 19.0, "y": 19.0, "z": 50.8}
    assert facts["volume_mm3"] == pytest.approx(9200.0)


def test_evaluate_flags_what_is_wrong():
    facts = json.loads((CASE_3 / "facts.json").read_text())
    good = {"ok": True, "bbox": [21.59, 132.08, 132.08], "volume": 177662.6, "area": 32105.0, "valid": True,
            "cyl_diameters": {"76.2": 1, "127.0": 1, "132.08": 1}, "iou": {"front": 0.98, "top": 0.99}}
    assert evaluate(good, facts).accepted
    solid = dict(good, volume=276000.0, cyl_diameters={"127.0": 1, "132.08": 1})
    ev = evaluate(solid, facts)
    assert not ev.accepted and ev.score < evaluate(good, facts).score
    text = "\n".join(ev.lines)
    assert "Too much material" in text and "d=76.2" in text
    failed = evaluate({"ok": False, "error": "NameError: x", "error_where": ["line 2: x"]}, facts)
    assert failed.score == 0 and "NameError" in failed.lines[0]


def test_trace_writes_dashboard_events(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE", "1")
    monkeypatch.setenv("TRACE_DIR", str(tmp_path))
    monkeypatch.setenv("TRACE_RUN_ID", "run1")
    tracer = Tracer(meta={"input_dir": "x"})
    case = tracer.case("test_case_1")
    case.start()
    with case.step("outer", kind="tool") as outer:
        with case.step("inner", kind="llm", input="hi") as inner:
            inner.done(output="yo", metrics={"score": 1})
        (case.dir / "a.stp").write_text("x")
        outer.artifact(case.dir / "a.stp", role="output")
    case.end("ok")
    tracer.finish("ok")
    events = [json.loads(line) for line in (tmp_path / "run1/test_case_1/events.jsonl").read_text().splitlines()]
    kinds = [e["t"] for e in events]
    assert kinds == ["case_start", "step_start", "step_start", "step_end", "artifact", "step_end", "case_end"]
    assert events[2]["parent"] == events[1]["id"] and events[3]["output"] == "yo"
    assert events[4]["path"] == "a.stp" and events[4]["role"] == "output"
    run = json.loads((tmp_path / "run1/run.json").read_text())
    assert run["status"] == "ok" and run["meta"]["input_dir"] == "x"


def test_tracing_is_off_by_default(monkeypatch):
    monkeypatch.delenv("TRACE", raising=False)
    tracer = Tracer()
    case = tracer.case("test_case_1")
    assert not tracer.enabled and not case.enabled
    with case.step("anything") as step:   # must be a harmless no-op
        step.log("x")
        step.done(output=1)


def test_agent_corrects_itself_and_writes_one_solid(tmp_path, monkeypatch):
    cq = pytest.importorskip("cadquery")
    import time

    from agentic_cad.agent import CadAgent

    monkeypatch.delenv("TRACE", raising=False)
    monkeypatch.setenv("AGENT_ENGINE", "legacy")   # the scripted client drives the fixed loop
    client = ScriptedClient([SLEEVE_NO_BORE, SLEEVE])
    agent = CadAgent(Settings.from_env(), client=client)
    output = tmp_path / "out" / "test_case_3.stp"
    summary = agent.generate_cad(
        name="test_case_3", case_dir=CASE_3, description=(CASE_3 / "description.md").read_text(),
        views=sorted((CASE_3 / "images").glob("view_*.png")), context=None, output_path=output,
        deadline=time.time() + 300, trace=Tracer().case("test_case_3"))

    assert summary["written"] and summary["attempt"] == 2 and summary["status"] == "ok"
    assert len(client.calls) == 2                       # stopped as soon as the part was accepted
    feedback = prompts.describe_for_trace(client.calls[1][-1]["content"])
    assert "Too much material" in feedback and "d=76.2" in feedback and "[image]" in feedback
    solid = cq.importers.importStep(str(output)).val()
    assert len(solid.Solids()) == 1 and solid.isValid()
    assert solid.Volume() == pytest.approx(177662.6, rel=1e-3)


def test_agent_falls_back_to_envelope_block_when_the_llm_is_down(tmp_path, monkeypatch):
    cq = pytest.importorskip("cadquery")
    import time

    from agentic_cad.agent import CadAgent
    from agentic_cad.llm import LLMError

    class DownClient:
        def chat(self, *args, **kwargs):
            raise LLMError("connection refused")

    monkeypatch.delenv("TRACE", raising=False)
    monkeypatch.setenv("AGENT_ENGINE", "legacy")   # the scripted client drives the fixed loop
    output = tmp_path / "test_case_3.stp"
    summary = CadAgent(Settings.from_env(), client=DownClient()).generate_cad(
        name="test_case_3", case_dir=CASE_3, description="", views=[], context=None, output_path=output,
        deadline=time.time() + 300, trace=Tracer().case("test_case_3"))
    assert summary["written"] and summary["attempt"] == 0 and summary["status"] == "error"
    bb = cq.importers.importStep(str(output)).val().BoundingBox()
    assert (bb.xlen, bb.ylen, bb.zlen) == pytest.approx((21.59, 132.08, 132.08), abs=1e-3)
