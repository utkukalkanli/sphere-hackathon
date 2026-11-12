"""Prompt text and message construction for the CAD agent."""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path

from agentic_cad.cadlib import HELPER_DOC

SYSTEM_PROMPT = f"""You are a senior mechanical CAD engineer. You reconstruct one mechanical part as a \
CadQuery (Python) script from a written design brief, a facts sheet measured on the real part, and \
8 rendered views. Your script is executed, the resulting solid is measured against the facts and its \
outline is compared with the views, and you get the differences back so you can correct it.

## Output contract
- Reply with a short analysis (a few lines), then ONE complete script in a single ```python fenced block.
- The script must assign the finished part to a variable named `result` (a cq.Workplane or a solid).
- Exactly ONE connected solid, in millimetres. Every feature must overlap or touch the main body.
- No file I/O, no imports other than `math` and `cadquery as cq` (both are already imported).
- Always send the full script again, never a diff.

## Coordinate frame
- All positions in the brief and facts are in mm from the MINIMUM corner of the part's bounding box, \
so build the part inside the box (0,0,0)-(X,Y,Z) of the envelope using those coordinates directly.
- The views are orthographic: front = looking along +Y (X right, Z up); back = looking along -Y \
(X to the LEFT, Z up); left = looking along +X (Y to the LEFT, Z up); right = looking along -X \
(Y right, Z up); top = looking along -Z (X right, Y up); bottom = looking along +Z (X right, Y DOWN); \
the two iso views are from (+X,-Y,+Z) and from (-X,+Y,-Z).
- Handedness matters: do not mirror the part.

## Helpers already defined for you (absolute coordinates, each returns a cq.Workplane)
{HELPER_DOC}
Combine with `a.union(b)`, `a.cut(b)`, `a.intersect(b)`. Plain CadQuery is also available as `cq`.

## How to work
1. Decide the part's base form first: extruded profile (use the view that shows the profile, then \
prism()), body of revolution (revolve() with the profile read from a side view), or a block with features.
2. Trust in this order: facts sheet numbers > what the views show > the prose of the brief. The brief \
is sometimes wrong about counts, symmetry, or which face a feature is on; the views are not.
3. Use the facts as a checklist: bounding box, every listed hole/bore/boss diameter with its axis, \
centre and length, and the target volume. A "length" of a cylindrical face tells you where a feature \
starts and stops (centre +- length/2 along its axis).
4. Check the volume in your head: if the solid envelope shape gives far more volume than the target, \
the part is hollow, thin-walled or deeply pocketed, so model the cavity.
5. Make cutters longer than the material they pass through (start 1 mm outside) so no skin is left.
6. Leave small fillets, rounds and chamfers for last and use try_fillet()/try_chamfer(); skip them \
when unsure. Overall shape, proportions and large features matter far more than edge breaks.
7. Keep the script simple and robust: named dimensions at the top, one feature per statement.

## Example of the expected style (a different part)
```python
L, W, H = 80.0, 50.0, 20.0                      # envelope X, Y, Z
body = box(0, 0, 0, L, W, 8.0)                  # base plate, 8 thick
body = body.union(cyl('z', 30.0, 0, H, 40.0, 25.0))          # central boss d30, full height
body = body.cut(cyl('z', 16.0, -1, H + 1, 40.0, 25.0))       # through bore d16
for x, y in [(10, 10), (70, 10), (10, 40), (70, 40)]:        # 4 mounting holes d6 through the plate
    body = body.cut(cyl('z', 6.0, -1, 9.0, x, y))
body = body.cut(box(30, -1, 14, 50, 12, H + 1))              # slot across the boss top
result = try_fillet(body, 1.0, "|Z")
```
"""

VIEW_CAPTIONS = {
    "front": "front (looking along +Y: X right, Z up)",
    "back": "back (looking along -Y: X left, Z up)",
    "left": "left (looking along +X: Y left, Z up)",
    "right": "right (looking along -X: Y right, Z up)",
    "top": "top (looking along -Z: X right, Y up)",
    "bottom": "bottom (looking along +Z: X right, Y down)",
    "iso_front_right_top": "isometric from +X, -Y, +Z",
    "iso_back_left_bottom": "isometric from -X, +Y, -Z",
}


def image_part(path: Path, max_px: int) -> dict:
    """An OpenAI-style image content part, downscaled so 8 views fit a modest context window."""
    from PIL import Image

    img = Image.open(path).convert("RGB")
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    data = base64.b64encode(buffer.getvalue()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


def view_name(path: Path) -> str:
    stem = re.sub(r"^view_\d+_", "", path.stem)
    return stem


def initial_message(description: str, facts: dict, views: list[Path], context: Path | None,
                    use_images: bool, image_px: int) -> list[dict]:
    text = ["# Design brief", description.strip(), ""]
    if facts:
        text += ["# Facts sheet (measured on the real part; positions from the bounding-box min corner)",
                 "```json", json.dumps(facts, separators=(",", ":")), "```", ""]
        box, volume = facts.get("bounding_box_mm") or {}, facts.get("volume_mm3")
        if volume and all(k in box for k in "xyz"):
            fill = float(volume) / max(float(box["x"]) * float(box["y"]) * float(box["z"]), 1e-9)
            text.append(f"The part fills {100 * fill:.0f}% of its bounding box"
                        + (" (mostly empty: thin walls, large openings or slender shape)." if fill < 0.25 else "."))
    content: list[dict] = [{"type": "text", "text": "\n".join(text)}]
    if use_images and views:
        content.append({"type": "text", "text": "# Rendered views of the target part"})
        for path in views:
            name = view_name(path)
            content.append({"type": "text", "text": f"View: {VIEW_CAPTIONS.get(name, name)}"})
            content.append(image_part(path, image_px))
        if context is not None:
            content.append({"type": "text", "text": "Context only: the part highlighted in orange inside its assembly."})
            content.append(image_part(context, image_px))
    content.append({"type": "text", "text": "Write the CadQuery script for this part now."})
    return content


def feedback_message(attempt: int, lines: list[str], sheets: list[Path], attempts_left: int,
                     best_note: str | None, use_images: bool) -> list[dict]:
    text = [f"# Result of attempt {attempt}", *lines, ""]
    if best_note:
        text += [best_note, ""]
    text.append("Fix the largest discrepancy first (overall shape and proportions, then big features, then holes, "
                "then edge details). Explain in two or three lines what you will change, then send the full "
                f"corrected script. Attempts left: {attempts_left}.")
    content: list[dict] = [{"type": "text", "text": "\n".join(text)}]
    if use_images and sheets:
        content.append({"type": "text", "text": "Top row: TARGET views. Bottom row: YOUR MODEL from the same cameras."})
        for path in sheets:
            content.append(image_part(path, 1400))
    return content


def extract_code(reply: str) -> str | None:
    """The last fenced python block of a reply (after any <think> section)."""
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    reply = reply.split("</think>")[-1]
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", reply, flags=re.S)
    blocks = [b for b in blocks if "result" in b or "cq." in b or "box(" in b or "cyl(" in b]
    if blocks:
        return blocks[-1].strip() + "\n"
    unfinished = re.search(r"```(?:python|py)?[ \t]*\n(.*)$", reply, flags=re.S)   # reply cut off mid-block
    if unfinished and "result" in unfinished.group(1):
        return unfinished.group(1).strip() + "\n"
    if "result =" in reply and "```" not in reply:
        return reply.strip() + "\n"
    return None


def describe_for_trace(content) -> str:
    """Message content with images replaced by a placeholder, for logs and traces."""
    if isinstance(content, str):
        return content
    out = []
    for part in content:
        out.append(part["text"] if part.get("type") == "text" else "[image]")
    return "\n".join(out)


# -- graph engine (tool-calling agent, text LLM + separate vision model) ------------------------------

_CONTRACT_END = SYSTEM_PROMPT.index("## Coordinate frame")
GRAPH_SYSTEM_PROMPT = SYSTEM_PROMPT[:SYSTEM_PROMPT.index("## Output contract")] + """## How you work here
You cannot see images yourself. You get a text description of the 8 views written by a vision model, and \
you act through tools:
- `build_and_evaluate(code, change)`: runs your full CadQuery script (`change` = one line on what you changed), measures the solid against the facts and \
compares its outline with the target views. Returns a score (1.0 = perfect) and the differences. The best \
solid so far is kept automatically, so a worse attempt never loses anything.
- `ask_vision(question, views, compare_last)`: asks the vision model a precise question about named target \
views (front, back, left, right, top, bottom, iso_front_right_top, iso_back_left_bottom), or, with \
compare_last=true, shows it your last model next to the target so it can say what differs. Ask short, \
concrete questions ("how many holes are visible in the top view and where?"). It is a small model: \
treat its answers as hints and prefer the facts sheet when they disagree.
- `submit(reason)`: finish when the checks are within tolerance or you cannot improve further.
Start building early: call build_and_evaluate in your first turn, then iterate on its feedback, fixing the \
largest discrepancy first (overall shape, then big features, then holes, then edge details).

## Script contract (the `code` argument)
- A complete script that assigns the finished part to `result` (a cq.Workplane or a solid). Always the full \
script, never a diff.
- Exactly ONE connected solid, in millimetres. Every feature must overlap or touch the main body.
- No file I/O, no imports other than `math` and `cadquery as cq` (both are already imported).

""" + SYSTEM_PROMPT[_CONTRACT_END:]

PERCEIVE_PROMPT = """You are looking at 8 rendered views of ONE mechanical part (grey solid on white). \
Envelope: {envelope}. View conventions: {conventions}

Describe the part's geometry for a CAD engineer who cannot see the images. Be factual and only state what \
you can see; say "unclear" rather than guessing. Answer in this format:
BASE FORM: extruded profile / body of revolution / block with features / other (which axis?)
PROFILE: which view shows the defining profile, and its shape (corners, arcs, steps, notches)
FEATURES: one line per feature: type (through hole, blind hole, slot, pocket, boss, rib, flange, fillet, \
chamfer, ...), which face/view, approximate position as a fraction of the width/height in that view, count
HOLLOW: is the part hollow, thin-walled or shelled? (yes/no/unclear)
SYMMETRY: mirror planes or rotational symmetry you can see
Keep it under 250 words."""

VISION_QUESTION_PROMPT = """You are looking at rendered views of a mechanical part (grey solid on white). \
View conventions: {conventions}
{what}
Question: {question}
Answer briefly and concretely (positions as fractions of the view width/height, counts, which side). \
Say "unclear" if you cannot tell."""


def view_conventions() -> str:
    return "; ".join(VIEW_CAPTIONS.values())


def envelope_text(facts: dict) -> str:
    box = facts.get("bounding_box_mm") or {}
    if all(k in box for k in "xyz"):
        return f"{float(box['x']):g} x {float(box['y']):g} x {float(box['z']):g} mm (X x Y x Z)"
    return "unknown"


def text_brief(description: str, facts: dict, context_note: bool) -> str:
    """The brief and facts as plain text (initial_message without images)."""
    parts = initial_message(description, facts, [], None, use_images=False, image_px=0)
    text = "\n".join(p["text"] for p in parts[:-1])
    if context_note:
        text += "\n\nThe part comes from an assembly (a context image exists; ask_vision can't see it)."
    return text


# -- hull engine (the outline is given; the model only removes hidden material) ----------------------

CARVE_SYSTEM_PROMPT = f"""You are a senior mechanical CAD engineer finishing a part in CadQuery (Python).

A solid named `base` already exists. It was computed from the part's front, right and top outlines, so its \
outer shape and bounding box are exactly right in those three directions, and openings that go straight \
through the part along X, Y or Z are already in it. Holes and bores located by the facts sheet may already \
be cut as well (you are told which). What `base` still gets wrong is material that no outline can show: it \
is solid wherever the real part has pockets, recesses, blind holes, counterbores, steps hidden behind taller \
features, grooves, or a hollow interior. Your job is to remove that material.

## Output contract
- Reply with two or three lines of reasoning, then ONE complete script in a single ```python fenced block.
- The script starts from `base` and must assign the finished part to `result`, for example \
`body = base` ... `result = body`.
- Only REMOVE material: `body = body.cut(<solid>)`. Never rebuild the part from scratch, never union \
anything, never move or scale `base`.
- Keep ONE connected solid: a cut must not split the part or remove a whole wall.
- No imports, no file I/O. `cq` and `math` are available. Always send the full script, never a diff.

## Coordinate frame
- All positions are in mm from the MINIMUM corner of the bounding box: the part sits inside the box \
(0,0,0)-(X,Y,Z) of the envelope and `base` is already there.
- front = looking along +Y (X right, Z up); right = looking along -X (Y right, Z up); top = looking along \
-Z (X right, Y up).

## Helpers (absolute coordinates, each returns a solid you can pass to .cut())
{HELPER_DOC}
## How to work
1. The volume tells you how much is left to remove: `base` volume minus target volume. Account for that \
amount with the features the brief describes, largest first.
2. If `base` is several times heavier than the target, the part is hollow, shelled or thin-walled: cut the \
cavity (a box, cylinder or revolve() profile that leaves the wall thickness given in the brief).
3. Use the facts sheet for exact numbers: a cylindrical face's centre and length give where a bore starts \
and stops (centre +- length/2 along its axis).
4. Start a cutter 1 mm outside the face it enters from, so no skin is left; stop it at the stated depth.
5. One feature per statement, with a comment. Prefer a few large, certain cuts over many doubtful ones: a \
cut in the wrong place is worse than a missing one.

## Example (a different part)
```python
body = base
body = body.cut(box(6, 6, 4, 74, 44, 21))                    # top pocket 68 x 38, floor at Z = 4
body = body.cut(cyl('z', 16.0, 12, 21, 40.0, 25.0))          # counterbore d16, 9 deep from the top
body = body.cut(cyl('x', 10.0, -1, 30, 25.0, 12.0))          # blind cross bore d10 from the X = 0 face
result = body
```
"""


def carve_message(description: str, facts: dict, base_lines: list[str], bores: list[str], best_code: str | None,
                  best_lines: list[str], failures: list[str]) -> str:
    text = "\n".join(p["text"] for p in initial_message(description, facts, [], None, use_images=False, image_px=0)[:-1])
    text += "\n\n# The solid `base` you start from\n" + "\n".join(base_lines)
    if bores:
        text += "\nAlready cut into `base` from the facts sheet (do not cut these again):\n" + "\n".join(
            f"- {line}" for line in bores[:40])
    if best_code:
        text += ("\n\n# Best script so far\n```python\n" + best_code.strip() + "\n```\nIts result:\n"
                 + "\n".join(best_lines)
                 + "\nImprove it: keep what works and fix the largest remaining difference.")
    if failures:
        text += "\n\n# Scripts that were rejected (do not repeat them)\n" + "\n".join(f"- {line}" for line in failures[-6:])
    text += "\n\nWrite the complete script now: start from `base`, remove the hidden material, assign `result`."
    return text


# -- hull engine, feature mode (the model fills in a JSON schema; features.py builds the geometry) ----

def _feature_system_prompt() -> str:
    from agentic_cad.features import MAX_FEATURES, SCHEMA_DOC

    return f"""You are a senior mechanical CAD engineer finishing a part. You do not write code: you list \
the features to cut and a CAD program builds them.

A solid named `base` already exists. It was computed from the part's front, right and top outlines, so its \
outer shape and bounding box are exactly right in those three directions, and openings that go straight \
through the part along X, Y or Z are already in it. Holes and bores located by the facts sheet may already \
be cut as well (you are told which). What `base` still gets wrong is material that no outline can show: it \
is solid wherever the real part has pockets, recesses, blind holes, counterbores, steps hidden behind taller \
features, grooves, or a hollow interior. Your job is to list that material so it can be removed.

## Output
One JSON object: {{"plan": "...", "features": [...]}}.
- "plan": two to four sentences. Say how many mm3 are left to remove (`base` volume minus target volume) and \
which features of the brief account for it, largest first.
- "features": at most {MAX_FEATURES} volumes to REMOVE from `base`. Each one is cut on its own; a feature \
that would split the part, open a through-hole the part does not have or take the volume below the target \
is skipped, so one wrong feature does not spoil the others. An empty list is a valid answer when nothing \
is hidden.
- Every feature has a "label" (what it is in the brief) and a "type" with these fields:
{SCHEMA_DOC}
## Coordinate frame
- All positions are in mm from the MINIMUM corner of the bounding box: the part sits inside the box \
(0,0,0)-(X,Y,Z) of the envelope and `base` is already there.
- front = looking along +Y (X right, Z up); right = looking along -X (Y right, Z up); top = looking along \
-Z (X right, Y up).

## How to work
1. If `base` is several times heavier than the target, the part is hollow, shelled or thin-walled: remove \
the cavity (a box, cylinder or revolve profile that leaves the wall thickness given in the brief).
2. Use the facts sheet for exact numbers: a cylindrical face's centre and length give where a bore starts \
and stops (centre +- length/2 along its axis).
3. Start a feature 1 mm outside the face it enters from, so no skin is left; stop it at the stated depth.
4. Prefer a few large, certain features over many doubtful ones: material removed in the wrong place is \
worse than a missing feature. Never remove material that an outline shows.

## Example (a different part)
{{"plan": "base is 31400 mm3 too heavy. The top pocket (68 x 38 x 17 = 43900 minus what the outlines already \
show) accounts for most of it, the counterbore and the blind cross bore for the rest.",
 "features": [
  {{"label": "top pocket 68 x 38, floor at Z = 4", "type": "box", "min": [6, 6, 4], "max": [74, 44, 22]}},
  {{"label": "counterbore d16, 9 deep from the top", "type": "cylinder", "diameter": 16, \
"start": [40, 25, 12], "end": [40, 25, 22]}},
  {{"label": "blind cross bore d10 from the X = 0 face", "type": "cylinder", "diameter": 10, \
"start": [-1, 25, 12], "end": [30, 25, 12]}}
 ]}}
"""


FEATURE_SYSTEM_PROMPT = _feature_system_prompt()


def feature_message(description: str, facts: dict, base_lines: list[str], bores: list[str], best: str | None,
                    best_lines: list[str], failures: list[str]) -> str:
    text = "\n".join(p["text"] for p in initial_message(description, facts, [], None, use_images=False, image_px=0)[:-1])
    text += "\n\n# The solid `base` you start from\n" + "\n".join(base_lines)
    if bores:
        text += "\nAlready cut into `base` from the facts sheet (do not list these again):\n" + "\n".join(
            f"- {line}" for line in bores[:40])
    if best:
        text += ("\n\n# Best feature list so far\n" + best + "\nIts result:\n" + "\n".join(best_lines)
                 + "\nImprove it: keep the features that were cut, fix or drop the skipped ones, and add what "
                   "accounts for the largest remaining difference.")
    if failures:
        text += "\n\n# Feature lists that were rejected (do not repeat them)\n" + "\n".join(
            f"- {line}" for line in failures[-6:])
    text += "\n\nReturn the JSON object now: the complete list of features to remove from `base`."
    return text


def view_parts(views: list[Path], image_px: int) -> list[dict]:
    """The reference views as captioned image parts, for a model that can see them."""
    parts: list[dict] = [{"type": "text", "text": "# Rendered views of the real part (grey solid on white). `base` "
                          "matches the front, right and top outlines; look for what lies inside them: pockets, "
                          "recesses, steps, blind holes and hollow regions, and where they are."}]
    for path in views:
        name = view_name(path)
        parts.append({"type": "text", "text": f"View: {VIEW_CAPTIONS.get(name, name)}"})
        parts.append(image_part(path, image_px))
    return parts
