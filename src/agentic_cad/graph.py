"""LangGraph engine: a tool-calling CAD agent that combines a text LLM with a separate vision model.

    fallback -> perceive -> agent <-> tools -> END
                              \\-> nudge (model stopped without a successful build) -> agent

- fallback: a plain envelope block is written before any model call, so every case has an output.
- perceive: the vision model (VLM_BASE_URL) turns the 8 views into a structured text description, because
  the text LLM cannot see images.
- agent: the text LLM (OPENAI_BASE_URL) decides what to do next by calling tools:
  build_and_evaluate(code), ask_vision(question, views, compare_last), submit(reason).
- tools: runs the calls. Builds reuse the legacy machinery (agent.make_attempt / publish), so the best
  solid so far is always on disk. The case stops on submit, when every check is within tolerance, when
  AGENT_MAX_STEPS LLM turns are used, or when the case's time budget runs out.

Every node and tool runs inside a trace step, so the dashboard (ui/server.py) shows the whole run.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import (AIMessage, AnyMessage, BaseMessage, HumanMessage, SystemMessage,
                                     ToolMessage)
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from agentic_cad import prompts
from agentic_cad.agent import Attempt, fallback_script, load_facts, make_attempt, publish
from agentic_cad.cadlib import HELPER_DOC
from agentic_cad.config import Settings
from agentic_cad.trace import CaseTrace

STOP_MARGIN_S = 45        # do not start an LLM turn with less time than this left
KEEP_RECENT = 6           # messages kept verbatim after the brief (older tool exchanges are dropped)
MAX_VISION_CALLS = 4      # ask_vision calls per case; the small VLM's answers repeat quickly
MAX_STALL = 3             # identical scripts in a row: the model is stuck, end the case
VIEW_KEYS = ["front", "back", "left", "right", "top", "bottom", "iso_front_right_top", "iso_back_left_bottom"]


class GraphState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    turns: int
    nudged: bool
    stop: str | None


@dataclass
class CaseContext:
    """Everything the nodes and tools of one case share. Lives outside the graph state: it holds paths,
    the trace and the best attempt, none of which belong in the message history."""
    settings: Settings
    name: str
    case_dir: Path
    description: str
    views: dict[str, Path]
    context: Path | None
    output_path: Path
    deadline: float
    trace: CaseTrace
    work: Path
    facts: dict
    best: Attempt | None = None          # best solid so far, including the fallback block
    best_llm: Attempt | None = None      # best solid the model built
    last: Attempt | None = None
    attempts: int = 0
    accepted: bool = False
    submitted: str | None = None
    perception: str = ""
    history: list[str] = field(default_factory=list)      # one line per build, always shown to the model
    seen_code: dict[str, Attempt] = field(default_factory=dict)   # normalised script -> that attempt
    vision_calls: int = 0
    since_build: int = 0      # tool calls since the last build (blocks ask_vision loops)
    repeats: int = 0          # duplicate scripts sent; raises the temperature
    stall: int = 0            # duplicate scripts in a row (reset by a real build)
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})

    def remaining(self) -> float:
        return self.deadline - time.time()


# -- model clients -------------------------------------------------------------------------------

def _chat(base_url: str, api_key: str, model: str, s: Settings, timeout: float, *, thinking: bool | None,
          max_tokens: int, temperature: float) -> ChatOpenAI:
    extra = {"chat_template_kwargs": {"enable_thinking": thinking}} if thinking is not None else None
    return ChatOpenAI(base_url=base_url, api_key=api_key, model=model, temperature=temperature,
                      max_tokens=max_tokens, timeout=max(5.0, timeout), max_retries=1, extra_body=extra)


def llm_for(ctx: CaseContext, model: str, timeout: float) -> ChatOpenAI:
    s = ctx.settings
    temperature = min(0.9, s.temperature + 0.2 * ctx.repeats)
    return _chat(s.base_url, s.api_key, model, s, timeout, thinking=s.enable_thinking, max_tokens=s.max_tokens,
                 temperature=temperature)


def vlm_for(ctx: CaseContext, model: str, timeout: float) -> ChatOpenAI:
    s = ctx.settings
    return _chat(s.vlm_base_url, s.vlm_api_key, model, s, timeout, thinking=None, max_tokens=900, temperature=0.1)


_model_cache: dict[str, str] = {}


def served_model(base_url: str, api_key: str, configured: str) -> str:
    """The configured model, or the first one the endpoint serves."""
    if configured:
        return configured
    if base_url not in _model_cache:
        import requests
        response = requests.get(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        response.raise_for_status()
        _model_cache[base_url] = response.json()["data"][0]["id"]
    return _model_cache[base_url]


def _usage(ctx: CaseContext, message: BaseMessage) -> dict:
    meta = getattr(message, "usage_metadata", None) or {}
    out = {"prompt_tokens": meta.get("input_tokens", 0), "completion_tokens": meta.get("output_tokens", 0)}
    for key, value in out.items():
        ctx.usage[key] += value
    return out


# -- tools ---------------------------------------------------------------------------------------

def make_tools(ctx: CaseContext):
    s = ctx.settings

    @tool
    def build_and_evaluate(code: str, change: str = "") -> str:
        """Run a complete CadQuery script that assigns the finished part to `result`, measure the solid
        against the facts sheet and compare its outline with the target views. Returns the score
        (1.0 = perfect) and the list of differences to fix. `change`: one line saying what this version
        changes compared to the previous one (or "first version")."""
        key = _normalise(code)
        if key in ctx.seen_code:
            ctx.repeats += 1
            ctx.since_build += 1
            ctx.stall += 1
            prev = ctx.seen_code[key]
            lines = [f"This script is identical to attempt {prev.number}, so it was not run again. Its result was:",
                     *prev.evaluation.lines]
            if ctx.stall == 1:
                lines.append("Change the geometry to address these differences (different dimensions, a feature added "
                             "or removed, a feature moved). Do not resend the same script.")
            else:
                lines += ["You have now sent this script more than once. Rewrite it FROM SCRATCH with a different "
                          "approach: use ONLY the helper functions below (no cq.Workplane methods), start from the "
                          "envelope box and add or cut one feature per line.", HELPER_DOC]
            return "\n".join(lines)
        ctx.since_build = 0
        ctx.stall = 0
        ctx.attempts += 1
        number = ctx.attempts
        with ctx.trace.step(f"build attempt {number}", kind="code") as step:
            timeout = max(10.0, min(s.exec_timeout_s, ctx.remaining() - 10))
            attempt = make_attempt(number, code, ctx.work, ctx.case_dir, ctx.facts, s, timeout, step)
        ctx.last = attempt
        ctx.seen_code[key] = attempt
        ctx.history.append(_history_line(attempt, change))
        lines = [f"Attempt {number}: " + ("built OK" if attempt.report.get("ok") else "FAILED"),
                 *attempt.evaluation.lines]
        if not attempt.report.get("ok"):
            lines += ["Fix: build the part with the helper functions below (exact signatures, absolute coordinates in "
                      "mm) instead of raw cq.Workplane calls, then send the complete corrected script.", HELPER_DOC]
        if attempt.report.get("ok"):
            lines.append(f"Score: {attempt.evaluation.score:.3f} (silhouette match {attempt.evaluation.rank:.3f}; the "
                         "silhouette match decides which attempt is kept)")
            if ctx.best_llm is None or attempt.evaluation.rank > ctx.best_llm.evaluation.rank:
                ctx.best_llm = attempt
                if ctx.best is None or attempt.evaluation.rank >= ctx.best.evaluation.rank:
                    ctx.best = attempt
                    publish(attempt, ctx.output_path, ctx.trace)
                lines.append("This is your best attempt so far.")
            else:
                lines.append(f"Worse silhouette match than attempt {ctx.best_llm.number} "
                             f"({ctx.best_llm.evaluation.rank:.3f}). Go back to that version and improve it instead:"
                             f"\n```python\n{ctx.best_llm.code}```")
            if attempt.evaluation.accepted:
                ctx.accepted = True
                lines.append("ACCEPTED: every check is within tolerance. The case is finished.")
        lines.append(f"Time left for this part: {ctx.remaining():.0f} s.")
        return "\n".join(lines)

    @tool
    def ask_vision(question: str, views: list[str] | None = None, compare_last: bool = False) -> str:
        """Ask the vision model a short, concrete question about the target part's rendered views.
        `views`: names among front, back, left, right, top, bottom, iso_front_right_top,
        iso_back_left_bottom (default: all). With compare_last=true it also sees your last built model
        next to the target (top row target, bottom row yours) and can say what differs."""
        if not s.vlm_base_url:
            return "No vision model is configured: rely on the facts sheet and the description."
        if ctx.vision_calls >= MAX_VISION_CALLS:
            return "The vision budget for this part is used up. Work from the build feedback and the facts sheet."
        if ctx.since_build >= 1 and ctx.attempts > 0:
            return ("You already asked since your last build. Build a new version first, using what you learned, "
                    "then ask again if needed.")
        ctx.vision_calls += 1
        ctx.since_build += 1
        names = [v for v in (views or VIEW_KEYS) if v in ctx.views] or list(ctx.views)
        content: list[dict] = []
        if compare_last and ctx.last is not None and ctx.last.report.get("sheets"):
            what = "The images are comparison sheets: top row TARGET views, bottom row the CANDIDATE model."
            for sheet in ctx.last.report["sheets"]:
                content.append(prompts.image_part(ctx.work / sheet, 1400))
        else:
            if compare_last:
                question += " (no candidate rendering is available; answer from the target views)"
            what = "The images are views of the TARGET part."
            for key in names:
                content.append({"type": "text", "text": f"View: {prompts.VIEW_CAPTIONS.get(key, key)}"})
                content.append(prompts.image_part(ctx.views[key], s.prompt_image_px))
        text = prompts.VISION_QUESTION_PROMPT.format(conventions=prompts.view_conventions(), what=what,
                                                     question=question)
        with ctx.trace.step("ask vision", kind="llm", input=question) as step:
            timeout = min(60.0, ctx.remaining() - 20)
            if timeout < 10:
                return "No time left for the vision model."
            model = served_model(s.vlm_base_url, s.vlm_api_key, s.vlm_model)
            reply = vlm_for(ctx, model, timeout).invoke([HumanMessage(content=[{"type": "text", "text": text},
                                                                               *content])])
            step.done(output=reply.content, metrics=_usage(ctx, reply))
        return f"Vision model (a hint, may be wrong): {reply.content}"

    @tool
    def submit(reason: str) -> str:
        """Finish this part. Call it when every check is within tolerance or you cannot improve further."""
        if ctx.best_llm is None:
            return "You have not built a valid solid yet. Call build_and_evaluate with a script first."
        ctx.submitted = reason
        return f"Submitted attempt {ctx.best_llm.number} (silhouette match {ctx.best_llm.evaluation.rank:.3f})."

    return [build_and_evaluate, ask_vision, submit]


# -- graph ---------------------------------------------------------------------------------------

def _normalise(code: str) -> str:
    """Script text without comments and blank lines, so cosmetic edits count as repeats."""
    lines = []
    for line in code.splitlines():
        line = line.split("#", 1)[0].rstrip()
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def _history_line(attempt: Attempt, change: str) -> str:
    ev = attempt.evaluation
    if not attempt.report.get("ok"):
        return f"#{attempt.number} FAILED ({str(attempt.report.get('error'))[:120]}) | change: {change or '?'}"
    parts = [f"#{attempt.number} score {ev.score:.3f}"]
    if attempt.report.get("bbox"):
        parts.append("bbox " + "x".join(f"{v:.1f}" for v in attempt.report["bbox"]))
    if ev.volume_err is not None:
        parts.append(f"vol {100 * ev.volume_err:+.0f}%")
    if ev.iou_min is not None:
        parts.append(f"iou min {ev.iou_min:.2f}")
    if ev.missing_diameters:
        parts.append(f"{len(ev.missing_diameters)} diameters missing")
    return ", ".join(parts) + f" | change: {change or '?'}"


def _window(messages: list[AnyMessage], ctx: CaseContext) -> list[AnyMessage]:
    """System + brief + attempt history + the most recent exchanges. The cut never orphans a ToolMessage
    from its call."""
    head, rest = messages[:2], messages[2:]
    start = max(0, len(rest) - KEEP_RECENT)
    while start < len(rest) and not isinstance(rest[start], AIMessage):
        start += 1
    if not ctx.history:
        return messages if start == 0 else head + rest[start:]
    best = ctx.best_llm
    note = ["# Attempt history (all builds so far)", *ctx.history]
    if best is not None:
        note.append(f"Best: attempt {best.number} (silhouette match {best.evaluation.rank:.3f}). Start from it, change one or "
                    "two things that address its largest differences, and never resend a script you already tried.")
    if start:
        note.append(f"({start} older messages omitted.)")
    # the note goes right after the brief so the recent exchange stays intact
    return head + [HumanMessage(content="\n".join(note)), AIMessage(content="Understood.")] + rest[start:]


def build_graph(ctx: CaseContext):
    s = ctx.settings
    tools = make_tools(ctx)
    by_name = {t.name: t for t in tools}

    def perceive(state: GraphState) -> dict:
        brief = prompts.text_brief(ctx.description, ctx.facts, context_note=False)
        if s.vlm_base_url and s.use_images and ctx.views:
            content = [{"type": "text", "text": prompts.PERCEIVE_PROMPT.format(
                envelope=prompts.envelope_text(ctx.facts), conventions=prompts.view_conventions())}]
            for key, path in ctx.views.items():
                content.append({"type": "text", "text": f"View: {prompts.VIEW_CAPTIONS.get(key, key)}"})
                content.append(prompts.image_part(path, s.prompt_image_px))
            if ctx.context is not None:
                content.append({"type": "text", "text": "Context only: the part highlighted in orange in its assembly."})
                content.append(prompts.image_part(ctx.context, s.prompt_image_px))
            with ctx.trace.step("perceive views", kind="llm", input="8 views -> text description") as step:
                try:
                    model = served_model(s.vlm_base_url, s.vlm_api_key, s.vlm_model)
                    reply = vlm_for(ctx, model, min(90.0, max(10.0, ctx.remaining() / 4))).invoke(
                        [HumanMessage(content=content)])
                    ctx.perception = str(reply.content).strip()
                    step.done(output=ctx.perception, metrics=_usage(ctx, reply))
                except Exception as exc:   # the agent still works from the brief and facts
                    step.log(f"vision model failed: {type(exc).__name__}: {exc}", "warn")
        if ctx.perception:
            brief += ("\n\n# What the views show (written by a small vision model: hints, may be wrong; the facts "
                      "sheet wins)\n" + ctx.perception)
        else:
            brief += "\n\n(No description of the views is available; rely on the brief and the facts sheet.)"
        brief += "\n\nBuild the part now with build_and_evaluate."
        return {"messages": [SystemMessage(content=prompts.GRAPH_SYSTEM_PROMPT), HumanMessage(content=brief)]}

    def agent(state: GraphState) -> dict:
        remaining = ctx.remaining()
        if remaining < STOP_MARGIN_S:
            ctx.trace.log(f"stopping: {remaining:.0f} s left", "warn")
            return {"stop": "time"}
        if state["turns"] >= s.max_steps:
            ctx.trace.log(f"stopping: {s.max_steps} LLM turns used", "warn")
            return {"stop": "steps"}
        window = _window(state["messages"], ctx)
        turn = state["turns"] + 1
        with ctx.trace.step(f"agent turn {turn}", kind="llm",
                            input=str(window[-1].content)[:4000]) as step:
            model = served_model(s.base_url, s.api_key, s.model)
            llm = llm_for(ctx, model, min(s.llm_timeout_s, remaining - 30))
            if ctx.stall:
                # the model keeps emitting the same tool call: ask for the script as plain text instead, which
                # samples differently, and let _repair turn the ```python block into the build call
                step.log("stalled on an identical tool call: requesting the script as text", "warn")
                window = window + [HumanMessage(content="Write the complete corrected script now as a ```python "
                                                        "block (no tool call).")]
            else:
                llm = llm.bind_tools(tools)
            reply: AIMessage = llm.invoke(window)
            reply = _repair(reply, step)
            calls = [f"{c['name']}({', '.join(c['args'])})" for c in reply.tool_calls]
            step.done(output={"text": reply.content, "tool_calls": calls}, metrics=_usage(ctx, reply))
        return {"messages": [reply], "turns": turn}

    def run_tools(state: GraphState) -> dict:
        last: AIMessage = state["messages"][-1]
        out = []
        for call in last.tool_calls:
            fn = by_name.get(call["name"])
            if fn is None:
                result = f"Unknown tool {call['name']}. Available: {', '.join(by_name)}."
            else:
                try:
                    result = fn.invoke(call["args"])
                except Exception as exc:
                    result = f"Tool error: {type(exc).__name__}: {exc}"
                    ctx.trace.log(f"{call['name']} failed: {result}", "error")
            out.append(ToolMessage(content=str(result), tool_call_id=call["id"], name=call["name"]))
            if ctx.accepted or ctx.submitted:
                break
        # every tool call needs an answer, even the ones skipped after the case finished
        answered = {m.tool_call_id for m in out}
        out += [ToolMessage(content="skipped: the case is finished", tool_call_id=c["id"], name=c["name"])
                for c in last.tool_calls if c["id"] not in answered]
        stop = "accepted" if ctx.accepted else ("submitted" if ctx.submitted else None)
        if stop is None and ctx.stall >= MAX_STALL:
            ctx.trace.log(f"stopping: the same script was sent {ctx.stall + 1} times in a row", "warn")
            stop = "stalled"
        return {"messages": out, "stop": stop}

    def nudge(state: GraphState) -> dict:
        return {"nudged": True, "messages": [HumanMessage(
            content="You have not built a valid solid yet. Call build_and_evaluate with a complete script.")]}

    def after_agent(state: GraphState) -> str:
        if state.get("stop"):
            return END
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        if ctx.best_llm is None and not state.get("nudged"):
            return "nudge"
        return END

    graph = StateGraph(GraphState)
    graph.add_node("perceive", perceive)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_node("nudge", nudge)
    graph.add_edge(START, "perceive")
    graph.add_edge("perceive", "agent")
    graph.add_conditional_edges("agent", after_agent, ["tools", "nudge", END])
    graph.add_conditional_edges("tools", lambda st: END if st.get("stop") else "agent", ["agent", END])
    graph.add_edge("nudge", "agent")
    return graph.compile()


def _repair(reply: AIMessage, step) -> AIMessage:
    """Small models sometimes put the script in the reply text, or emit tool-call JSON that does not parse.
    Turn a python block into a build_and_evaluate call so the turn is not wasted."""
    if reply.tool_calls:
        return reply
    text = str(reply.content or "")
    for bad in reply.invalid_tool_calls or []:
        text += "\n" + str(bad.get("args") or "")
    code = prompts.extract_code(text)
    if code is None:
        if reply.invalid_tool_calls:   # drop unparseable calls: they would need tool answers we cannot give
            step.log("unparseable tool call dropped", "warn")
            return AIMessage(content=str(reply.content or "") + "\n(my tool call was malformed)",
                             usage_metadata=reply.usage_metadata)
        return reply
    step.log("script found in the reply text: converted to a build_and_evaluate call", "warn")
    return AIMessage(content=str(reply.content or ""), usage_metadata=reply.usage_metadata,
                     tool_calls=[{"name": "build_and_evaluate", "args": {"code": code},
                                  "id": f"repair_{int(time.time() * 1000)}", "type": "tool_call"}])


# -- entry point ---------------------------------------------------------------------------------

def _views_by_key(views: list[Path]) -> dict[str, Path]:
    out = {}
    for path in views:
        name = prompts.view_name(path)
        out[name if name in VIEW_KEYS else path.stem] = path
    return out


def run_case(settings: Settings, name: str, case_dir: Path, description: str, views: list[Path],
             context: Path | None, output_path: Path, deadline: float, trace: CaseTrace) -> dict:
    """Same contract as CadAgent.generate_cad: always writes output_path when anything valid exists."""
    work = trace.dir if trace.enabled else Path(tempfile.mkdtemp(prefix=f"{name}_"))
    ctx = CaseContext(settings=settings, name=name, case_dir=case_dir, description=description,
                      views=_views_by_key(views), context=context, output_path=output_path, deadline=deadline,
                      trace=trace, work=work, facts=load_facts(case_dir, description))
    trace.start()
    status, error, stop = "ok", None, None
    try:
        with trace.step("fallback block", kind="code") as step:
            ctx.best = make_attempt(0, fallback_script(ctx.facts), work, case_dir, ctx.facts, settings,
                                    min(60.0, settings.exec_timeout_s), step)
            publish(ctx.best, output_path, step)
        app = build_graph(ctx)
        final = app.invoke({"messages": [], "turns": 0, "nudged": False, "stop": None},
                           config={"recursion_limit": 4 * settings.max_steps + 10})
        stop = final.get("stop") or "model finished"
        trace.log(f"graph ended: {stop}")
    except Exception as exc:   # never let one case take the run down
        status, error = "error", f"{type(exc).__name__}: {exc}"
        trace.log(error, "error")
    finally:
        if ctx.best is not None and ctx.best.step_path is not None:
            trace.artifact(ctx.best.step_path, role="output")
        trace.end(status if output_path.is_file() else "error", error)
        if not trace.enabled:
            shutil.rmtree(work, ignore_errors=True)
    summary = {"name": name, "status": status, "error": error, "written": output_path.is_file(),
               "attempt": ctx.best.number if ctx.best else None, "builds": ctx.attempts, "stop": stop,
               **ctx.usage}
    if ctx.best is not None:
        summary.update(ctx.best.evaluation.metrics())
    return summary
