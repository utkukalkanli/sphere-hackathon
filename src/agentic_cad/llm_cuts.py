"""Ask a model for the cuts the silhouettes and facts.json cannot give: pockets, recesses, slots, angled
faces inside the outline. It answers with numbers only (points, centres, axes); the geometry is built
and checked in code (`features`), so a wrong answer can cost time but never a broken part.

Endpoint: OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL (README: Configuration). LLM_VISION=1 also
sends the two isometric renders with the coordinate system drawn on them, for a vision model.
"""

from __future__ import annotations

import base64
import json
import os
import re

import cv2
import numpy as np
import requests

from agentic_cad.features import Cut

TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT", 120))
MAX_CUTS = 40

SYSTEM = """You are a CAD engineer. A solid has already been made from the part's silhouettes: it is \
the largest solid whose outline matches the part in all eight views, and every hole listed in \
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
- at most %d cuts.""" % MAX_CUTS


def configured() -> bool:
    return bool(os.environ.get("OPENAI_MODEL"))


def _image_part(image: np.ndarray) -> dict:
    ok, png = cv2.imencode(".png", image)
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


def build_messages(brief: str, facts: dict, drilled: list[Cut], overlays: dict[str, np.ndarray]) -> list[dict]:
    keep = {k: v for k, v in facts.items() if not k.startswith("_")}
    text = (f"# Brief\n{brief}\n\n# facts.json\n{json.dumps(keep, indent=1)}\n\n"
            f"# Already cut\n{json.dumps([c.describe() for c in drilled], indent=1)}\n\n"
            "List the remaining cuts.")
    content: list[dict] = [{"type": "text", "text": text}]
    if os.environ.get("LLM_VISION") == "1":
        for name, image in overlays.items():
            content.append({"type": "text", "text": f"{name}: the render with the X (red), Y (green), Z (blue) "
                                                    "axes and the bounding box drawn on, ticks in mm"})
            content.append(_image_part(image))
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


def call(messages: list[dict]) -> tuple[str, dict]:
    base = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    response = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"},
        json={"model": os.environ["OPENAI_MODEL"], "messages": messages,
              "temperature": float(os.environ.get("LLM_TEMPERATURE", 0)), "max_tokens": 6000},
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"] or "", data.get("usage") or {}


def parse(answer: str) -> list[Cut]:
    """The cuts in a model answer; malformed entries are dropped, not fatal."""
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S)
    match = re.search(r"\{.*\}", answer, flags=re.S)
    if not match:
        raise ValueError("no JSON object in the answer")
    cuts = []
    for index, item in enumerate(json.loads(match.group(0)).get("cuts") or []):
        if not isinstance(item, dict) or item.get("kind") not in ("box", "cylinder", "prism"):
            continue
        params = {k: v for k, v in item.items() if k not in ("name", "kind")}
        cuts.append(Cut(name=str(item.get("name") or f"cut {index + 1}")[:80], kind=item["kind"], params=params,
                        source="llm", group=f"llm_{index}"))
    return cuts[:MAX_CUTS]
