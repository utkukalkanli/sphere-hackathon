"""Pocket floors a model reads off the views: the depth no silhouette, line or tone gives us.

For each straight view the areas between the view's lines (`regions.py`) are numbered and drawn onto
the view and onto our solid as the same camera would show it. A vision model gets those two pictures,
the two isometric views, the brief and facts.json, and a table of the areas with where our solid's
surface lies over each. It answers which areas are floors lying deeper than our surface and at which
coordinate. Our code cuts the column over such an area down to that floor (as `depth.py` does with
levels it finds itself). The outline always comes from the view; the model gives only the depth.

A model's depth is inferred, not stated by the brief or facts.json, so a cut is kept only if the
verifier does not rate the solid lower and at least MIN_SHARE of the true volume is left
(decisions.md). A model also proposes nonsense (practice 8: a whole column through the part, which
the verifier let through), so a floor must also be a flat face square to the view by its tone, lie
inside the part and not at its far side, and the cut must make our solid's shading agree with the 8
views on more pixels without making the silhouettes agree on fewer. Off unless OPENAI_MODEL is set; HULL_MODEL=0 switches it off.
Endpoint: OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

import cv2
import numpy as np
import requests
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import depth, regions, tone, trace, verify
from agentic_cad import visual_hull as vh

MIN_REGION_PX = 1500     # smaller areas are not offered to the model
MAX_REGIONS = 20         # per view, the largest ones
MIN_EXCESS = 1.02        # the step runs only on a solid with at least this much over the true volume
MIN_SHARE = 0.995        # a cut must leave this share of the true volume (a right one leaves all of it)
MIN_TONE_GAIN_PX = 200   # over the 8 views, this many fewer pixels of ours must differ in tone from the view
MAX_FLOORS = 6           # per view, at most this many of the model's floors are tried
MIN_DROP_PX = 2.0        # a floor must lie this many pixels below our surface to be cut
SCORE_SLACK = 0.003      # the verifier's score may drop by this much and the cut still be kept
MAX_SINGLE = 6           # when a view's cuts fail together, this many are tried one by one
TONE_OFF = 15            # grey levels: our tone and the view's differ (as tools/check_views.py)
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", 60))
CUT_TIMEOUT = float(os.environ.get("HULL_MODEL_CUT_TIMEOUT", 30))
BUDGET = float(os.environ.get("HULL_MODEL_BUDGET", 150))   # seconds per case at most
last_log: list[dict] = []   # what the model was shown and answered in the last case, for tools
COLOURS = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180), (70, 240, 240),
           (240, 50, 230), (128, 128, 0), (0, 0, 128), (170, 110, 40)]

SYSTEM = """You are a CAD engineer checking a 3D model against the drawings of a mechanical part.

You get one orthographic view of the part with its areas outlined and numbered (the areas between \
the drawn lines), the same view of OUR current model with the same numbers, the part's two isometric \
views, its brief and facts.json. Our model was carved from the silhouettes, so it is never smaller \
than the part, but it fills in pockets, recesses and cavities that are open towards this view: there \
our surface lies too close to the camera.

Your task: find the numbered areas that are the FLOOR of such an opening (a flat face, square to \
this view, lying deeper than our surface there), and give the coordinate of that floor along the \
view axis. Use the isometric views to judge how deep each opening goes, and the brief and facts.json \
for any stated depth or thickness (a wall thickness, a pocket depth, a plate thickness).

Answer with one JSON object and nothing else:
{"floors": [{"area": <number>, "floor": <coordinate in mm>, "reason": "<a few words>"}]}
- "floor" is the coordinate along the axis named in the table (for example Z for the top view), in \
the part's frame (0 to the envelope);
- only areas whose floor is deeper than our surface (further from the camera); leave out areas where \
our model is already right, and areas that are not flat floors (walls seen edge-on, curved faces);
- a floor at the far side of the part would be an opening right through it; every such opening is already cut from the silhouettes, so never give one;
- when unsure, leave it out: a missed floor costs little, a wrong one is rejected;
- if nothing is to be cut in this view, answer {"floors": []}."""


def configured() -> bool:
    return bool(os.environ.get("OPENAI_MODEL")) and os.environ.get("HULL_MODEL", "1") != "0"


def call(messages: list[dict], timeout: float = TIMEOUT) -> str:
    base = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    response = requests.post(f"{base}/chat/completions",
                             headers={"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"},
                             json={"model": os.environ["OPENAI_MODEL"], "messages": messages, "temperature": 0,
                                   "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", 2000))},
                             timeout=timeout)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"] or ""


def parse(answer: str) -> list[dict]:
    """The floors in a model answer; malformed entries are dropped."""
    answer = re.sub(r"<think>.*?(</think>|$)", "", answer, flags=re.S)
    match = re.search(r"\{.*\}", answer, flags=re.S)
    if not match:
        return []
    try:
        floors = json.loads(match.group(0)).get("floors") or []
    except (json.JSONDecodeError, AttributeError):
        return []
    result = []
    for item in floors:
        try:
            result.append({"area": int(item["area"]), "floor": float(item["floor"]),
                           "reason": str(item.get("reason", ""))[:120]})
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _image(image: np.ndarray) -> dict:
    ok, png = cv2.imencode(".png", image)
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


def _axis(vector: np.ndarray) -> tuple[int, float]:
    index = int(np.argmax(np.abs(vector)))
    return index, float(np.sign(vector[index]))


def _numbered(image: np.ndarray, labels: np.ndarray, offered: list[dict]) -> np.ndarray:
    picture = image.copy() if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    for item in offered:
        colour = COLOURS[(item["area"] - 1) % len(COLOURS)]
        contours, _ = cv2.findContours((labels == item["label"]).astype(np.uint8), cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(picture, contours, -1, colour, 3)
    for item in offered:   # numbers last, so no outline covers them
        colour = COLOURS[(item["area"] - 1) % len(COLOURS)]
        text, (x, y) = str(item["area"]), item["at"]
        (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.4, 3)
        cv2.rectangle(picture, (x - w // 2 - 4, y - h // 2 - 6), (x + w // 2 + 4, y + h // 2 + 6), (255, 255, 255), -1)
        cv2.putText(picture, text, (x - w // 2, y + h // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.4, colour, 3)
    return picture


def offer(view: verify.Observed, drawing: verify.Drawing, labels: np.ndarray, found: list[regions.Region],
          dims: dict[str, float], table) -> list[dict]:
    """The areas shown to the model, with where our surface lies over each."""
    right, up, towards = verify.camera(view.name)
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    half = view.mask.shape[0] / 2
    axis, sign = _axis(towards)
    seen = view.image.max(axis=2).astype(float)
    ours = np.where(drawing.mask, table.tone(tone.in_camera(drawing.normal, view.name)), 255.0)
    core = cv2.erode((labels > 0).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    items = []
    for region in sorted(found, key=lambda r: -r.area):
        if region.area < MIN_REGION_PX or len(items) >= MAX_REGIONS:
            continue
        area = labels == region.label
        inside = area & core & drawing.mask
        if inside.sum() < MIN_REGION_PX / 3:
            continue
        surface = centre[axis] + sign * drawing.depth[inside]   # our surface, as a coordinate along the axis
        rows, cols = np.nonzero(inside)
        corners = []
        for col, row in ((cols.min(), rows.max()), (cols.max(), rows.min())):
            point = centre + (col + 0.5 - half) * view.mm_per_px * right + (half - 0.5 - row) * view.mm_per_px * up
            corners.append(point)
        span = {"xyz"[i].upper(): [round(float(min(corners[0][i], corners[1][i])), 1),
                                    round(float(max(corners[0][i], corners[1][i])), 1)]
                for i in range(3) if i != axis}
        # a point well inside the area for its number
        inner = cv2.distanceTransform(area.astype(np.uint8), cv2.DIST_L2, 5)
        row, col = np.unravel_index(int(np.argmax(inner)), inner.shape)
        items.append({"area": len(items) + 1, "label": region.label, "at": (int(col), int(row)),
                      "size_mm2": round(float(area.sum()) * view.mm_per_px ** 2),
                      "extent": span, "kind": region.kind,
                      "our_surface": [round(float(np.percentile(surface, 5)), 1), round(float(np.percentile(surface, 95)), 1)],
                      "tone_differs": round(float((np.abs(seen[inside] - ours[inside]) > TONE_OFF).mean()), 2)})
    return items


def ask(view: verify.Observed, drawing: verify.Drawing, labels: np.ndarray, items: list[dict],
        context: dict, isos: list[np.ndarray], table) -> tuple[list[dict], str]:
    right, up, towards = verify.camera(view.name)
    axis, sign = _axis(towards)
    name = "XYZ"[axis]
    r_axis, r_sign = _axis(right)
    u_axis, u_sign = _axis(up)
    ours = np.where(drawing.mask, table.tone(tone.in_camera(drawing.normal, view.name)), 255.0)
    ours = np.clip(ours, 0, 255).astype(np.uint8)
    ours[drawing.sharp] = 30
    table_rows = [{k: v for k, v in item.items() if k not in ("label", "at")} for item in items]
    text = (f"# Brief\n{context['brief']}\n\n# facts.json\n{context['facts']}\n\n"
            f"# Our model\nvolume {context['ratio']:.2f} times the part's true volume "
            f"({context['excess_cm3']:.1f} cm3 too much in all; the part is never larger than our model).\n\n"
            f"# This view: {view.name}\nThe camera looks along {'-' if sign > 0 else '+'}{name}; the image's right is "
            f"{'+' if r_sign > 0 else '-'}{'XYZ'[r_axis]}, its up is {'+' if u_sign > 0 else '-'}{'XYZ'[u_axis]}. "
            f"Nearer to the camera means {'larger' if sign > 0 else 'smaller'} {name}, deeper means "
            f"{'smaller' if sign > 0 else 'larger'} {name}. The part spans {name} 0 to {context['dims'][axis]:.2f}.\n"
            f"Areas (\"our_surface\": where our model's surface lies over the area, as {name}; \"tone_differs\": the "
            f"share of the area where our model's shading differs from the view's; \"kind\": what the view's shading "
            f"says the face is):\n{json.dumps(table_rows)}\n\n"
            f"Pictures: 1. the view with the numbered areas; 2. our model from the same camera, same numbers; "
            f"3. and 4. the part's isometric views (front-right-top and back-left-bottom).")
    content = [{"type": "text", "text": text}, _image(_numbered(view.image, labels, items)),
               _image(_numbered(ours, labels, items))] + [_image(iso) for iso in isos]
    answer = call([{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}])
    return parse(answer), answer


def cuts_for(view: verify.Observed, drawing: verify.Drawing, labels: np.ndarray, items: list[dict],
             floors: list[dict], dims: dict[str, float]) -> tuple[list[vh.Silhouette], list[dict]]:
    """Columns down to the model's floors; floors that are not below our surface are dropped."""
    right, up, towards = verify.camera(view.name)
    centre = np.array([dims["x"], dims["y"], dims["z"]]) / 2
    half = view.mask.shape[0] / 2
    axis, sign = _axis(towards)
    reach = 2 * float(np.linalg.norm(centre))
    by_number = {item["area"]: item for item in items}

    def to_mm(pixels: np.ndarray) -> np.ndarray:
        return np.column_stack([(pixels[:, 0] + 0.5 - half) * view.mm_per_px, (half - 0.5 - pixels[:, 1]) * view.mm_per_px])

    cuts, notes = [], []
    for floor in floors:
        item = by_number.get(floor["area"])
        if item is None:
            notes.append({**floor, "result": "no such area"})
            continue
        level = sign * (floor["floor"] - centre[axis])   # along `towards`, from the centre
        lowest = min(sign * (value - centre[axis]) for value in item["our_surface"])   # our deepest point there
        margin = 2 * view.mm_per_px
        if not margin <= floor["floor"] <= dims["xyz"[axis]] - margin:
            notes.append({**floor, "result": "at or outside the part's far side (that would be a through opening)"})
            continue
        if item["kind"] != "facing":
            notes.append({**floor, "result": f"the area is {item['kind']}, not a face square to the view"})
            continue
        if level > lowest - MIN_DROP_PX * view.mm_per_px:
            notes.append({**floor, "result": "not below our surface"})
            continue
        area = (labels == item["label"]).astype(np.uint8) * 255
        outlines = vh.trace_outlines(area, to_mm, view.mm_per_px)
        if not outlines:
            notes.append({**floor, "result": "no outline"})
            continue
        cuts.append(vh.Silhouette(name=f"model_{view.name}_{floor['area']}", views=[view.name], mask=area,
                                  outlines=outlines, origin=centre + level * towards, right=right, up=up,
                                  direction=towards, depth=reach,
                                  info={"area": floor["area"], "floor": floor["floor"], "pixels": int((area > 0).sum()),
                                        "reason": floor["reason"]}))
        notes.append({**floor, "result": "proposed"})
    return cuts[:MAX_FLOORS], notes


def view_error(solid: TopoDS_Shape, observed: dict, dims: dict[str, float], table, deflection: float) -> tuple[int, int]:
    """Pixels over the 8 views where our solid's shading differs from the view's, and pixels where the
    silhouettes differ (as tools/check_views.py counts them)."""
    mesh = verify.tessellate(solid, deflection)
    kernel = np.ones((7, 7), np.uint8)
    tone_off = mask_off = 0
    for name, view in observed.items():
        drawing = verify.draw(mesh, name, dims, view.mm_per_px)
        seen = view.image.max(axis=2).astype(float)
        ours = np.where(drawing.mask, table.tone(tone.in_camera(drawing.normal, name)), 255.0)
        near = cv2.dilate((view.lines | drawing.sharp).astype(np.uint8), kernel) > 0
        off = view.mask & drawing.mask & ~near & (np.abs(seen - ours) > TONE_OFF)
        tone_off += int((cv2.morphologyEx(off.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0).sum())
        mask_off += int((view.mask != drawing.mask).sum())
    return tone_off, mask_off


def _context(case_dir: Path, dims: tuple[float, float, float], solid: TopoDS_Shape, target: float | None) -> dict:
    brief = (case_dir / "description.md").read_text(encoding="utf-8") if (case_dir / "description.md").is_file() else ""
    try:
        facts = json.loads((case_dir / "facts.json").read_text())
        facts = json.dumps({k: v for k, v in facts.items() if not k.startswith("_")})
    except Exception:
        facts = "{}"
    volume = vh.volume(solid)
    return {"brief": brief, "facts": facts, "dims": dims, "ratio": volume / target if target else 1.0,
            "excess_cm3": (volume - target) / 1000 if target else 0.0}


def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    with trace.step("model_floors", kind="model",
                    description="A model reads the depth of pocket floors off the views; our code cuts them") as step:
        if case_dir is None or not configured():
            step.log("no model configured (OPENAI_MODEL)")
            return solid
        target = vh.read_volume(case_dir)
        if target and vh.volume(solid) < MIN_EXCESS * target:
            step.log("the solid is within 2 % of the true volume; nothing to look for")
            return solid
        started = time.time()
        budget = BUDGET if agent.deadline is None else min(BUDGET, 0.8 * (agent.deadline - started))
        dims_tuple = vh.read_dimensions(case_dir)
        dims = dict(zip("xyz", dims_tuple))
        observed = verify.observe(case_dir, dims)
        straight = [name for name in verify.ORTHO_VIEWS if name in observed]
        found = regions.find(observed)
        isos = [observed[name].image for name in ("iso_front_right_top", "iso_back_left_bottom") if name in observed]
        deflection = 0.3 * min(view.mm_per_px for view in observed.values())
        first = verify.check(solid, case_dir, observed)
        setup = time.time()
        score, start, kept, log = first.score, solid, 0, []
        drawings = {name: verify.draw(verify.tessellate(solid, deflection), name, dims, view.mm_per_px)
                    for name, view in observed.items()}
        table = tone.choose(observed, drawings) or tone.tables()["organisers"]

        error = view_error(solid, observed, dims, table, deflection)
        first_error = error

        def attempt(current, cuts):
            nonlocal score, error
            carved = agent._guarded(step, current, target,
                                    lambda: agent.cutter.run(depth.remove, current, cuts, CUT_TIMEOUT),
                                    quiet=True, share=MIN_SHARE)
            if carved is None or vh.volume(carved) >= vh.volume(current) * (1 - 1e-6):
                return None
            new_error = view_error(carved, observed, dims, table, deflection)
            if new_error[0] > error[0] - MIN_TONE_GAIN_PX or new_error[1] > error[1] * 1.001 + 50:
                step.log(f"{', '.join(c.name for c in cuts)}: the views do not agree better (tone off {error[0]} to "
                         f"{new_error[0]} px, silhouette off {error[1]} to {new_error[1]} px)", level="warning")
                return None
            after = verify.check(carved, case_dir, observed).score
            if after < score - SCORE_SLACK:
                step.log(f"{', '.join(c.name for c in cuts)}: the verifier rates it lower ({score:.3f} to {after:.3f})",
                         level="warning")
                return None
            score, error = after, new_error
            return carved

        for name in straight:
            if agent.out_of_time() or time.time() - started > budget:
                step.log("out of time; the remaining views are not asked about", level="warning")
                break
            view, view_started = observed[name], time.time()
            drawing = verify.draw(verify.tessellate(solid, deflection), name, dims, view.mm_per_px)
            labels, found_here = found[name]
            items = offer(view, drawing, labels, found_here, dims, table)
            if not items:
                continue
            context = _context(case_dir, dims_tuple, solid, target)
            asked = time.time()
            try:
                floors, answer = ask(view, drawing, labels, items, context, isos, table)
            except Exception as error:
                step.log(f"{name}: the model call failed: {error!r}", level="warning")
                continue
            cuts, notes = cuts_for(view, drawing, labels, items, floors, dims)
            log.append({"view": name, "areas": [{k: v for k, v in i.items() if k not in ("label", "at")} for i in items],
                        "answer": answer[:4000], "floors": notes,
                        "seconds": {"draw": round(asked - view_started, 1), "model": round(time.time() - asked, 1)}})
            if not cuts:
                continue
            cut_started = time.time()
            step.log(f"{name}: the model gives {len(cuts)} floor(s): "
                     + ", ".join(f"area {c.info['area']} to {c.info['floor']}" for c in cuts))
            carved = attempt(solid, cuts)
            if carved is not None:
                solid, kept = carved, kept + len(cuts)
            elif len(cuts) > 1:
                for cut in sorted(cuts, key=lambda c: -c.info["pixels"])[:MAX_SINGLE]:
                    carved = attempt(solid, [cut])
                    if carved is not None:
                        solid, kept = carved, kept + 1
            log[-1]["seconds"]["cuts"] = round(time.time() - cut_started, 1)
        global last_log
        last_log = log
        step.attach("model_floors.json", log)
        log.insert(0, {"setup_seconds": round(setup - started, 1)})
        log.insert(1, {"view_error": [list(first_error), list(error)]})
        step.metric(floors_kept=kept, score_before=round(first.score, 4), score_after=round(score, 4),
                    seconds=round(time.time() - started, 1))
        if solid is start:
            return solid
        agent._record(step, solid, "model")
        return solid
