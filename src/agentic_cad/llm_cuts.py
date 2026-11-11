"""Ask a model for the cuts the silhouettes and facts.json cannot give: pockets, recesses, slots, angled
faces inside the outline. It answers with numbers only (points, centres, axes); the geometry is built
and checked in code (`features`, `hull_agent`), so a wrong answer can cost time but never a broken part.

Off unless OPENAI_MODEL is set. Endpoint: OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL.
LLM_VISION=1 also sends the two isometric renders with the coordinate system drawn on them, for a
vision model. LLM_TIMEOUT caps one request (seconds); the agent may give it less. LLM_THINKING=1 lets
a reasoning model (Qwen3) think first; it is off by default because the thinking alone used up 6000
tokens and 25 s on practice part 1 without reaching an answer. LLM_MAX_TOKENS caps the answer.
"""

from __future__ import annotations

import ast
import base64
import json
import operator
import os
import re

import cv2
import numpy as np
import requests

from agentic_cad.features import Cut

TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT", 60))
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", 6000))
THINKING = os.environ.get("LLM_THINKING") == "1"
MAX_CUTS = 40

SYSTEM = """You are a CAD engineer. A solid has already been made from the part's silhouettes: it is \
the largest solid whose outline matches the part in the six axis views, and every hole listed in \
facts.json has been drilled. What is still missing are cuts that no outline shows: pockets, \
recesses, blind slots, grooves, the inside of hollow bodies, cavities between ribs, angled faces \
inside the outline.

Coordinates: millimetres, the part's own X/Y/Z frame, the bounding box from (0,0,0) to the \
envelope. Views: front looks along +Y (X right, Z up), top looks along -Z (X right, Y up), right \
looks along -X (Y right, Z up). Positions in the brief are measured from the min corner.

Answer with one JSON object and nothing else:
{"cuts": [
  {"name": "...", "kind": "box", "min": [x, y, z], "max": [x, y, z]},
  {"name": "...", "kind": "cylinder", "centre": [x, y, z], "axis": [dx, dy, dz], "diameter": d, "length": l},
  {"name": "...", "kind": "prism", "points": [[x, y, z], ...], "direction": [dx, dy, dz], "depth": d}
]}
- every cut is material to REMOVE; never cut material the brief says is there;
- a cylinder is centred on "centre" and runs length/2 to each side along "axis";
- a prism is a flat polygon (its corners, in order, all in one plane: write every slanted edge as \
its two end points) swept by "depth" mm along "direction";
- only cuts the brief gives dimensions for; when unsure, leave it out (a missing cut costs little, a \
wrong one is rejected);
- write every number as a plain number (12.7, not 6.35 + 6.35);
- at most %d cuts. If nothing is left to cut, answer {"cuts": []}.""" % MAX_CUTS


def configured() -> bool:
    return bool(os.environ.get("OPENAI_MODEL"))


def _image_part(image: np.ndarray) -> dict:
    ok, png = cv2.imencode(".png", image)
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


def build_messages(brief: str, facts: dict, drilled: list[dict], overlays: dict[str, np.ndarray]) -> list[dict]:
    keep = {k: v for k, v in facts.items() if not k.startswith("_")}
    text = (f"# Brief\n{brief}\n\n# facts.json\n{json.dumps(keep, indent=1)}\n\n"
            f"# Holes already drilled\n{json.dumps(drilled, indent=1)}\n\n"
            "List the remaining cuts.")
    content: list[dict] = [{"type": "text", "text": text}]
    if os.environ.get("LLM_VISION") == "1":
        for name, image in overlays.items():
            content.append({"type": "text", "text": f"{name}: the render with the X (red), Y (green), Z (blue) "
                                                    "axes and the bounding box drawn on, ticks in mm"})
            content.append(_image_part(image))
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


def call(messages: list[dict], timeout: float = TIMEOUT_S) -> tuple[str, dict]:
    base = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    response = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"},
        json={"model": os.environ["OPENAI_MODEL"], "messages": messages,
              "temperature": float(os.environ.get("LLM_TEMPERATURE", 0)), "max_tokens": MAX_TOKENS,
              # vLLM passes this to the chat template; Qwen3 then answers without a <think> block
              "chat_template_kwargs": {"enable_thinking": THINKING}},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"] or "", data.get("usage") or {}


_OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
              ast.USub: operator.neg, ast.UAdd: operator.pos}
# a value that is arithmetic on numbers, e.g. "26.67 + 1.27" or "(66.04 - 12.7) / 2", between JSON delimiters
_EXPRESSION = re.compile(r"(?<=[\[:,])(\s*)([-+(\s]*\d[\d.\s]*(?:[-+*/()]+\s*[\d.]+[\d.\s)]*)+)(?=\s*[,\]}])")


def _arithmetic(text: str) -> float:
    """The value of plain arithmetic on numbers; anything else raises."""
    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](value(node.left), value(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](value(node.operand))
        raise ValueError(f"not arithmetic: {text!r}")
    return float(value(ast.parse(text.strip(), mode="eval").body))


def _evaluate_arithmetic(text: str) -> str:
    """Models sometimes write `26.67 + 1.27` where JSON needs a number: work those out."""
    def replace(match):
        try:
            return match.group(1) + repr(round(_arithmetic(match.group(2)), 6))
        except (ValueError, SyntaxError, ZeroDivisionError):
            return match.group(0)
    return _EXPRESSION.sub(replace, text)


def parse(answer: str) -> list[Cut]:
    """The cuts in a model answer; malformed entries are dropped, not fatal."""
    answer = re.sub(r"<think>.*?(</think>|$)", "", answer, flags=re.S)  # an unfinished one too
    match = re.search(r"\{.*\}", answer, flags=re.S)
    if not match:
        raise ValueError("no JSON object in the answer")
    cuts = []
    for index, item in enumerate(json.loads(_evaluate_arithmetic(match.group(0))).get("cuts") or []):
        if not isinstance(item, dict) or item.get("kind") not in ("box", "cylinder", "prism"):
            continue
        params = {k: v for k, v in item.items() if k not in ("name", "kind")}
        cuts.append(Cut(name=str(item.get("name") or f"cut {index + 1}")[:80], kind=item["kind"], params=params,
                        source="llm", group=f"llm_{index}"))
    return cuts[:MAX_CUTS]
