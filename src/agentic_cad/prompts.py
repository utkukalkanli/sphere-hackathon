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
