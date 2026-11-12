"""Scores a candidate against facts.json and the reference views, and words the feedback."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOL_BBOX = 0.005      # relative, per axis
TOL_VOLUME = 0.01
TOL_AREA = 0.03
GOOD_IOU = 0.95


@dataclass
class Evaluation:
    score: float = 0.0
    accepted: bool = False
    bbox_err: float | None = None
    volume_err: float | None = None    # signed, relative
    area_err: float | None = None      # signed, relative
    iou_mean: float | None = None
    iou_min: float | None = None
    missing_diameters: list = field(default_factory=list)
    lines: list = field(default_factory=list)   # human/LLM readable findings

    @property
    def rank(self) -> float:
        """What decides which attempt is kept and published. On the local eval set the mean silhouette IoU
        over the views tracks the volume IoU against the ground truth (Spearman +0.66 over 94 attempts),
        while the composite score does not (+0.12): matching the volume by removing material in the wrong
        place scores well and loses geometry. Falls back to the score when no views were compared."""
        return self.iou_mean if self.iou_mean is not None else self.score

    def metrics(self) -> dict:
        out = {"score": round(self.score, 4)}
        for key in ("bbox_err", "volume_err", "area_err", "iou_mean", "iou_min"):
            value = getattr(self, key)
            if value is not None:
                out[key] = round(value, 4)
        return out


_DASHES = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014"}   # the briefs use non-breaking hyphens


def facts_from_description(text: str) -> dict:
    """Fallback when a case has no facts.json: envelope and volume as written in the brief."""
    facts: dict = {}
    text = text.translate(_DASHES)
    num = r"(\d+(?:\.\d+)?)"
    m = re.search(rf"Envelope:\**\s*{num}\s*[x×]\s*{num}\s*[x×]\s*{num}\s*mm", text)
    if m:
        facts["bounding_box_mm"] = {k: float(v) for k, v in zip("xyz", m.groups())}
    m = re.search(rf"volume\s*{num}\s*cm", text)
    if m:
        facts["volume_mm3"] = float(m.group(1)) * 1000.0
    m = re.search(r"(\d+|no)\s*through[\s-]*(?:passages?|holes?)", text, re.IGNORECASE)
    if m:
        facts["through_holes_genus"] = 0 if m.group(1).lower() == "no" else int(m.group(1))
    return facts


def target_diameters(facts: dict) -> list[tuple[float, str]]:
    out = []
    for key, kind in (("cylindrical_holes_and_bores", "hole/bore"), ("cylindrical_shafts_and_bosses", "shaft/boss")):
        for item in facts.get(key) or []:
            try:
                out.append((float(item["diameter"]), f"{kind} d={item['diameter']} axis {item.get('axis', '?')}"
                                                     f" x{item.get('count', 1)}"))
            except (KeyError, TypeError, ValueError):
                pass
    return out


def evaluate(report: dict, facts: dict) -> Evaluation:
    ev = Evaluation()
    if not report.get("ok"):
        ev.lines.append(f"The script failed: {report.get('error')}")
        for line in report.get("error_where") or []:
            ev.lines.append(f"  at {line}")
        return ev

    parts: list[tuple[float, float]] = []   # (weight, value in 0..1)
    box = facts.get("bounding_box_mm") or {}
    if all(k in box for k in "xyz"):
        target = [float(box[k]) for k in "xyz"]
        got = report["bbox"]
        errs = [abs(g - t) / t if t else 0.0 for g, t in zip(got, target)]
        ev.bbox_err = max(errs)
        parts.append((0.20, max(0.0, 1.0 - 8.0 * sum(errs) / 3)))
        state = "OK" if ev.bbox_err <= TOL_BBOX else "WRONG"
        ev.lines.append(f"Bounding box [{state}]: yours {got[0]:.2f} x {got[1]:.2f} x {got[2]:.2f} mm, "
                        f"target {target[0]:.2f} x {target[1]:.2f} x {target[2]:.2f} mm")
        for axis, g, t, e in zip("XYZ", got, target, errs):
            if e > TOL_BBOX:
                ev.lines.append(f"  {axis} extent is {g - t:+.2f} mm off: something sticks out too far or is too short.")

    if facts.get("volume_mm3"):
        ev.volume_err = report["volume"] / float(facts["volume_mm3"]) - 1.0
        parts.append((0.30, max(0.0, 1.0 - 3.0 * abs(ev.volume_err))))
        state = "OK" if abs(ev.volume_err) <= TOL_VOLUME else "WRONG"
        ev.lines.append(f"Volume [{state}]: yours {report['volume']:.0f} mm3, target {float(facts['volume_mm3']):.0f} mm3 "
                        f"({100 * ev.volume_err:+.1f}%)")
    if facts.get("surface_area_mm2"):
        ev.area_err = report["area"] / float(facts["surface_area_mm2"]) - 1.0
        parts.append((0.15, max(0.0, 1.0 - 3.0 * abs(ev.area_err))))
        state = "OK" if abs(ev.area_err) <= TOL_AREA else "WRONG"
        ev.lines.append(f"Surface area [{state}]: yours {report['area']:.0f} mm2, target "
                        f"{float(facts['surface_area_mm2']):.0f} mm2 ({100 * ev.area_err:+.1f}%)")

    # what the pair (volume, area) says about the kind of error
    v, a = ev.volume_err, ev.area_err
    if v is not None and abs(v) > TOL_VOLUME:
        if v > 0.25 and a is not None and a < -0.10:
            ev.lines.append("  Far too much material with too little surface: the real part is hollow / thin-walled or has "
                            "large pockets you modelled as solid. Look for cavities in the views.")
        elif v > 0:
            ev.lines.append("  Too much material: a pocket, bore, recess or cut-out is missing or too shallow, or a wall "
                            "is too thick.")
        else:
            ev.lines.append("  Too little material: a boss, rib, web or flange is missing or too thin, or a cut is too "
                            "deep/too wide.")

    iou = report.get("iou") or {}
    if iou:
        values = list(iou.values())
        ev.iou_mean, ev.iou_min = sum(values) / len(values), min(values)
        parts.append((0.35, ev.iou_mean))
        ev.lines.append("Silhouette overlap with the target views (1.00 = identical outline): "
                        + ", ".join(f"{k} {v:.2f}" for k, v in iou.items()))
        bad = [k for k, v in iou.items() if v < GOOD_IOU]
        if bad:
            ev.lines.append(f"  Outline differs most in: {', '.join(sorted(bad, key=lambda k: iou[k]))}. Compare those "
                            "views: a feature is misplaced, missing, or on the wrong side.")

    have = [float(d) for d in (report.get("cyl_diameters") or {})]
    for diameter, label in target_diameters(facts):
        if not any(abs(diameter - h) <= max(0.02, 0.005 * diameter) for h in have):
            ev.missing_diameters.append(label)
    if ev.missing_diameters:
        ev.lines.append("Cylindrical features in the facts with no matching diameter in your model: "
                        + "; ".join(ev.missing_diameters[:12]))

    if report.get("solids_in_result", 1) != 1:
        parts.append((0.15, 0.0))
    for note in report.get("notes") or []:
        ev.lines.append(f"Note: {note}")
    if not report.get("valid", True):
        parts.append((0.20, 0.0))

    total = sum(w for w, _ in parts)
    ev.score = sum(w * x for w, x in parts) / total if total else 0.5
    ev.accepted = (
        report.get("valid", True) and report.get("solids_in_result", 1) == 1
        and (ev.bbox_err is None or ev.bbox_err <= TOL_BBOX)
        and (ev.volume_err is None or abs(ev.volume_err) <= TOL_VOLUME)
        and (ev.area_err is None or abs(ev.area_err) <= TOL_AREA)
        and (ev.iou_min is None or ev.iou_min >= 0.92)
        and not ev.missing_diameters
        and (ev.volume_err is not None or ev.iou_mean is not None)
    )
    return ev
