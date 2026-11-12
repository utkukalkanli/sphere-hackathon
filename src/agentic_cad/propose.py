"""A model proposes cuts; a deterministic judge decides.

A model can read structure from the views ("a pocket open to the top, down to about the bore") that
no step here finds, but its numbers are guesses. So it only proposes: typed cuts (ops.py), each of
which is made, judged against the views where it acts (judge.py, the local rule, whatever
HULL_JUDGE says) and kept only if accepted, if the views fit strictly better with it (a cut no
view shows is a guess and stays out), and if the file still reads back as one valid solid.

One request per case to an OpenAI-compatible endpoint: OPENAI_BASE_URL, OPENAI_API_KEY and
OPENAI_MODEL (README "Configuration"). **Without them the step does nothing and the pipeline is
what it is without a model.** A timeout, a refused connection or an answer that cannot be read
also mean no cut. HULL_PROPOSALS=<dir> reads `<dir>/test_case_<n>.json` instead of asking a model:
for tests and for replaying a saved answer.

Not measured against ground truth with a served model yet (docs/plan-pipeline.md, phase 3, step 0).
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import cv2
import numpy as np
from OCP.TopoDS import TopoDS_Shape

from agentic_cad import judge, ops, tone, trace, verify
from agentic_cad import visual_hull as vh

MAX_OPS = 24               # cuts tried per case, the most confident first
MAX_ROWS = 25              # regions per view in the table sent, the largest first
DEPTHS = (0.5, 0.75, 1.0, 1.25)   # of a proposed depth: tried shallowest first, the first accepted one is kept
MIN_EXCESS = 1.02          # nothing is asked about a solid within this of the part's volume
CUT_TIMEOUT = float(os.environ.get("HULL_MODEL_CUT_TIMEOUT", 30))
ASK_TIMEOUT = float(os.environ.get("HULL_MODEL_TIMEOUT", 180))
MAX_TOKENS = int(os.environ.get("HULL_MODEL_MAX_TOKENS", 4000))
# HULL_MODEL_TELL_VOLUME=0: the model is not told how much the solid holds too much (to see how much
# of a result depends on being told)
TELL_VOLUME = os.environ.get("HULL_MODEL_TELL_VOLUME", "1") != "0"

# Depths tried for a recess whose depth is open, as shares of what our solid still has under the
# region along the view (from its surface to the far side of the box)
SCAN = (0.03, 0.06, 0.1, 0.16, 0.25, 0.35, 0.5, 0.7, 0.9)
MAX_RECESSES = 12          # recesses whose depth is searched per case
SAME_ERROR = 0.02          # two depths whose errors differ by less than this share are equally good: the shallower wins

SYSTEM = """You compare engineering views of one mechanical part with a first solid model of it. \
The solid was carved from the part's bounding box by its silhouettes and by the holes listed in \
facts.json, so it contains the whole part and still has too much material wherever a pocket, \
recess, cavity, groove or step cannot be seen in a silhouette. You say WHERE material is still \
too much. You do not give depths. You answer with JSON only."""

INSTRUCTIONS = """You get: the six straight views of the part (front, back, left, right, top, bottom) \
with their regions outlined and numbered (a region is one area between the lines drawn on face \
boundaries); the two isometric views of the part; and, for each isometric view, OUR SOLID drawn \
from the same camera. Where the part's isometric view shows a hollow, a pocket, a step down or an \
opening and OUR SOLID shows a filled flat face there, our solid has too much material.

For each such place, name the numbered region of the straight view that looks into it: the region \
that is the FLOOR of the pocket or recess as seen from that view. Answer with a JSON object:
  {"recesses": [{"view": "top", "region": 7, "confidence": 0.8, "evidence": "one sentence"}, ...]}

Rules:
- Only regions of kind "facing" whose real surface lies DEEPER than in our solid. A region that is already right is not listed.
- Do not list the holes of facts.json: they are already cut. Do not give depths or coordinates.
- The table says for each region: kind (facing = flat and square to the camera, slanted, curved), \
area_px, below_px (length of drawn lines that lie under our surface there: a sign that the part has \
something our solid covers) and coplanar_with (neighbouring regions that our solid has in one plane \
although the view draws a line between them: one of the two is deeper in the real part).
- The brief can be wrong; where it disagrees with the views, the views decide.
- The largest recesses first. At most 12. JSON only, no other text."""


def replay_dir() -> Path | None:
    value = os.environ.get("HULL_PROPOSALS")
    return Path(value) if value else None


def enabled() -> bool:
    # HULL_MODEL_STEP=1: an endpoint alone no longer switches this step on, the harness uses the same
    # variables (with Gemma this step made the practice parts worse, 0.908 to 0.880)
    return replay_dir() is not None or bool(os.environ.get("HULL_MODEL_STEP") == "1"
                                            and os.environ.get("OPENAI_BASE_URL") and os.environ.get("OPENAI_MODEL"))


# -- asking ------------------------------------------------------------------------------------------

def _image(png: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


def table_for_model(rows: list[dict], evidence) -> list[dict]:
    """The rows of evidence.rows a model needs, with depths measured from the box face of the view."""
    out, sent = [], {}
    for row in rows:
        sent[row["view"]] = sent.get(row["view"], 0) + 1
        if sent[row["view"]] > MAX_ROWS:
            continue
        # no depth: a model given our own depth hands it back as its proposal
        out.append({"view": row["view"], "region": row["number"], "kind": row["kind"], "area_px": row["area_px"],
                    "below_px": row["below_px"], "coplanar_with": row["coplanar_with"]})
    return out


def shaded(evidence, solid: TopoDS_Shape, view: str) -> bytes:
    """Our solid from the camera of a view, as a PNG in the renderer's tones: for a reader to compare
    with the view itself."""
    seen, drawing = evidence.observed[view], evidence.drawings(solid)[view]
    table = evidence.table(solid)
    grey = np.full(drawing.mask.shape, 150.0)
    if table is not None:
        grey = np.clip(table.tone(tone.in_camera(drawing.normal, view)), 0, 255)
    grey = np.where(drawing.mask, grey, 255.0)
    grey[drawing.sharp] = 40
    tan = np.stack([grey * 0.48, grey * 0.79, grey], axis=2)   # BGR, about the renders' tan
    image = np.where(drawing.mask[:, :, None], tan, 255.0).astype(np.uint8)
    return cv2.imencode(".png", image)[1].tobytes()


def messages(case_dir: Path, evidence, rows: list[dict], ratio: float | None, solid: TopoDS_Shape | None = None) -> list[dict]:
    facts = (case_dir / "facts.json").read_text()
    brief = (case_dir / "description.md").read_text() if (case_dir / "description.md").is_file() else ""
    text = [INSTRUCTIONS, "\nfacts.json:\n" + facts, "\nBrief (may be wrong):\n" + brief,
            "\nRegions:\n" + json.dumps(table_for_model(rows, evidence))]
    if ratio is not None and TELL_VOLUME:
        text.append(f"\nOur solid holds {ratio:.2f} times the part's true volume.")
    content = [{"type": "text", "text": "\n".join(text)}]
    marked = evidence.marked()
    for name in verify.CAMERAS:
        if name in marked:
            content += [{"type": "text", "text": f"{name} view, regions numbered:"}, _image(marked[name])]
        elif name in evidence.observed:
            content += [{"type": "text", "text": f"{name} view of the PART:"},
                        _image(cv2.imencode(".png", evidence.observed[name].image)[1].tobytes())]
            if solid is not None:
                content += [{"type": "text", "text": f"OUR SOLID from the same camera ({name}):"},
                            _image(shaded(evidence, solid, name))]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


def ask(request: list[dict]) -> str | None:
    """The model's answer as text, or None on any failure."""
    import requests

    base = os.environ["OPENAI_BASE_URL"].rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}
    body = {"model": os.environ["OPENAI_MODEL"], "messages": request, "temperature": 0, "max_tokens": MAX_TOKENS}
    try:
        answer = requests.post(f"{base}/chat/completions", json=body, headers=headers, timeout=ASK_TIMEOUT)
        answer.raise_for_status()
        return answer.json()["choices"][0]["message"]["content"]
    except Exception as error:
        trace.log(f"the model could not be asked: {error!r}", level="warning")
        return None


def parse(text: str | None) -> list[dict]:
    """The ops in an answer: the JSON object or list in it, fenced or not. [] if there is none."""
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    for candidate in ([fenced.group(1)] if fenced else []) + [text]:
        start = min((i for i in (candidate.find("{"), candidate.find("[")) if i >= 0), default=-1)
        end = max(candidate.rfind("}"), candidate.rfind("]"))
        if start < 0 or end <= start:
            continue
        try:
            data = json.loads(candidate[start:end + 1])
        except ValueError:
            continue
        if isinstance(data, dict):   # {"ops": [...]} and/or {"recesses": [...]}: a recess needs no "type"
            found = [op for op in data.get("ops") or [] if isinstance(op, dict)]
            found += [{"type": "recess", **op} for op in data.get("recesses") or [] if isinstance(op, dict)]
            if "ops" in data or "recesses" in data:
                return found
            continue
        if isinstance(data, list):
            return [op for op in data if isinstance(op, dict)]
    return []


def proposals(step, case_dir: Path, evidence, solid: TopoDS_Shape, ratio: float | None, answers: list | None = None) -> list[dict]:
    """The normalised ops for this case, the most confident first, at most MAX_OPS. A model's answer
    is also appended to `answers`, if a tool gave a list (tools/check_judge.py)."""
    folder = replay_dir()
    if folder is not None:
        path = folder / f"{case_dir.name}.json"
        raw = parse(path.read_text()) if path.is_file() else []
        step.log(f"{len(raw)} op(s) read from {path}")
    else:
        request = messages(case_dir, evidence, evidence.rows(solid), ratio, solid)
        answer = ask(request)
        if answer is not None:
            step.attach("answer.txt", answer)
        if answers is not None:
            answers.append(answer)
        raw = parse(answer)
    clean = []
    for number, record in enumerate(raw, 1):
        try:
            op = ops.normalise(record)
        except (ValueError, KeyError, TypeError) as error:
            step.log(f"op {record.get('id', number)!r} cannot be read: {error!r}", level="warning")
            continue
        if op["id"] == "?":
            op["id"] = f"op{number}"
        clean.append(op)
    step.metric(proposed=len(raw), readable=len(clean))
    return sorted(clean, key=lambda op: -op["confidence"])[:MAX_OPS]


# -- applying ----------------------------------------------------------------------------------------

def apply(agent, solid: TopoDS_Shape, case_dir: Path | None, views: list[Path]) -> TopoDS_Shape:
    """The pipeline step. Without an endpoint (and without HULL_PROPOSALS) it returns the solid untouched."""
    if case_dir is None or not enabled():
        return solid
    with trace.step("model_cuts", kind="llm", description=(
            "A model proposes what to remove; each cut is kept only if the views agree where it acts")) as step:
        dims = dict(zip("xyz", vh.read_dimensions(case_dir)))
        target = vh.read_volume(case_dir)
        ratio = vh.volume(solid) / target if target else None
        if ratio is not None and ratio < MIN_EXCESS:
            step.log("the solid already has the part's volume; nothing to ask")
            return solid
        evidence = agent.evidence(case_dir, dims)
        pending = proposals(step, case_dir, evidence, solid, ratio, getattr(agent, "answers", None))
        if not pending:
            step.log("no cut was proposed")
            return solid
        rater = judge.Judge(case_dir, dims, evidence.observed)
        current, kept, report = solid, [], {}

        size = np.array([dims[k] for k in "xyz"])
        searched = [0]

        def depths_for(base, op) -> list[dict]:
            """A recess as region_floors at the depths to try: from our surface under the region down."""
            labels, items = evidence.regions()[op["view"]]
            if not 1 <= op["region"] <= len(items):
                raise ValueError(f"the {op['view']} view has no region {op['region']}")
            if items[op["region"] - 1].kind != "facing":
                # the column has a flat floor square to the view; under a curved or slanted region the
                # part has no such floor (eval 3, a tyre: four such cuts were kept and all took real material)
                raise ValueError(f"region {op['region']} is {items[op['region'] - 1].kind}, not a flat face square to the view")
            drawing = evidence.drawings(base)[op["view"]]
            under = drawing.depth[(labels == items[op["region"] - 1].label) & drawing.mask]
            if not len(under):
                raise ValueError("our solid has nothing under the region")
            extent = float(abs(verify.camera(op["view"])[2]) @ size)
            surface = extent / 2 - float(np.median(under))        # below the box face the view looks at
            return [{**op, "type": "region_floor", "depth": surface + share * (extent - surface)} for share in SCAN]

        def search(base, op):
            """A recess of open depth: every depth of SCAN is cut and judged; the one the views fit best
            is kept (of nearly equal ones the shallowest), if they fit strictly better than before."""
            if searched[0] >= MAX_RECESSES:
                report[op["id"]] = "not tried: enough recesses searched for this case"
                return None
            searched[0] += 1
            try:
                variants = depths_for(base, op)
            except ValueError as error:
                report[op["id"]] = f"cannot be built: {error}"
                return None
            rated, worse = [], 0
            for variant in variants:
                # shallow to deep: past the floor a cut only adds walls the part does not have, so two
                # depths in a row that are no better than the best so far end the search
                if agent.out_of_time() or (rated and worse >= 2):
                    break
                try:
                    tool = ops.tool_for(variant, evidence)
                except ValueError as error:
                    report[op["id"]] = f"cannot be built: {error}"
                    return None
                carved = agent._guarded(step, base, target, lambda: agent.cutter.run(ops.cut, base, tool, CUT_TIMEOUT), quiet=True)
                if carved is None or vh.volume(carved) >= vh.volume(base) * (1 - 1e-6):
                    continue
                verdict = rater.rate(base, carved)
                judge.note(agent, "model", base, carved, bool(verdict.accepted and verdict.error < 0), verdict)
                better = verdict.accepted and verdict.error < min((r[0] for r in rated), default=0.0)
                worse = 0 if better else worse + 1
                if verdict.accepted and verdict.error < 0:
                    rated.append((verdict.error, variant, carved, verdict))
            if not rated:
                report[op["id"]] = "no depth at which the views fit better"
                return None
            best = min(error for error, *_ in rated)
            error, variant, carved, verdict = next(r for r in rated if r[0] <= best * (1 - SAME_ERROR))
            if not agent.save(carved):
                report[op["id"]] = "the file does not read back as one valid solid"
                return None
            report[op["id"]] = f"kept at depth {variant['depth']:.2f} of {len(rated)} that fit ({verdict})"
            return carved

        def attempt(base, op):
            """The op at the shallowest of its depths the judge accepts, or None."""
            if op["type"] == "recess":
                return search(base, op)
            variants = [v for v in (ops.deeper(op, factor) for factor in DEPTHS) if v is not None] or [op]
            for variant in variants:
                if agent.out_of_time():
                    return None
                try:
                    tool = ops.tool_for(variant, evidence)
                except ValueError as error:
                    report[op["id"]] = f"cannot be built: {error}"
                    return None
                carved = agent._guarded(step, base, target, lambda: agent.cutter.run(ops.cut, base, tool, CUT_TIMEOUT), quiet=True)
                if carved is None or vh.volume(carved) >= vh.volume(base) * (1 - 1e-6):
                    report[op["id"]] = "no valid cut, nothing removed, or less than the part's volume left"
                    continue
                verdict = rater.rate(base, carved)
                judge.note(agent, "model", base, carved, verdict.accepted, verdict)
                if not verdict.accepted:
                    report[op["id"]] = f"{verdict.reason} ({verdict})"
                    continue
                if verdict.error >= 0:   # a guess needs the views to speak for it, not only not against it
                    report[op["id"]] = f"no view shows it ({verdict})"
                    continue
                if not agent.save(carved):
                    report[op["id"]] = "the file does not read back as one valid solid"
                    continue
                report[op["id"]] = f"kept ({verdict})" + (f" at depth {variant['depth']:.2f}" if "depth" in variant else "")
                return carved
            return None

        # Twice: a cut that showed nothing against the solid can show clearly once the recess above
        # it is open (docs/plan-pipeline.md, practice 7)
        for _ in range(2):
            refused, before = [], len(kept)
            for op in pending:
                if agent.out_of_time():
                    step.log("out of time for this case; the remaining proposals are not tried", level="warning")
                    refused = []
                    break
                carved = attempt(current, op)
                if carved is None:
                    refused.append(op)
                else:
                    current, kept = carved, kept + [op]
            pending = refused
            if not pending or len(kept) == before:
                break
        outcomes = [{**op, "outcome": report.get(op["id"], "not tried")} for op in kept + pending]
        step.attach("ops.json", outcomes)
        if getattr(agent, "answers", None) is not None:
            agent.answers.append(outcomes)
        step.metric(kept=len(kept), refused=len(pending))
        if not kept:
            step.log("no proposed cut was kept")
            return solid
        step._status = None
        after = vh.volume(current)   # the metrics so far are those of the last cut tried, kept or not
        step.metric(volume_cm3=round(after / 1000, 3), removed_cm3=round((vh.volume(solid) - after) / 1000, 3),
                    **({"vs_true_volume": round(after / target, 3)} if target else {}))
        agent._record(step, current, "model")
        return current
