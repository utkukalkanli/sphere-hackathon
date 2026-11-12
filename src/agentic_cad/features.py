"""Feature list instead of free code: the model fills in a JSON schema, this module builds the geometry.

The model never writes CadQuery here. It returns a list of material-removing features (pocket, bore, cone,
ring groove, polygon pocket, revolved cavity) with absolute coordinates, constrained by `SCHEMA` through the
endpoint's structured output, so a reply cannot call an API that does not exist. `compile_script` turns the
list into a script of cadlib calls on top of `base`; each feature is cut on its own and skipped, with the
reason recorded in `feature_log`, when it would break the part.
"""

from __future__ import annotations

import json
import math

MAX_FEATURES = 16
MAX_POINTS = 48

_NUM = {"type": "number"}
_POINT3 = {"type": "array", "items": _NUM, "minItems": 3, "maxItems": 3}
_POINT2 = {"type": "array", "items": _NUM, "minItems": 2, "maxItems": 2}
_LABEL = {"type": "string", "maxLength": 120}


def _feature(kind: str, properties: dict) -> dict:
    properties = {"label": _LABEL, "type": {"const": kind}, **properties}
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


FEATURES = {
    "box": _feature("box", {"min": _POINT3, "max": _POINT3}),
    "cylinder": _feature("cylinder", {"diameter": _NUM, "start": _POINT3, "end": _POINT3}),
    "cone": _feature("cone", {"diameter_start": _NUM, "diameter_end": _NUM, "start": _POINT3, "end": _POINT3}),
    "ring": _feature("ring", {"outer_diameter": _NUM, "inner_diameter": _NUM, "start": _POINT3, "end": _POINT3}),
    "prism": _feature("prism", {"plane": {"enum": ["xy", "xz", "yz"]},
                                "points": {"type": "array", "items": _POINT2, "minItems": 3, "maxItems": MAX_POINTS},
                                "from": _NUM, "to": _NUM}),
    "revolve": _feature("revolve", {"axis": {"enum": ["x", "y", "z"]}, "centre": _POINT2,
                                    "profile": {"type": "array", "items": _POINT2, "minItems": 3,
                                                "maxItems": MAX_POINTS}}),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "plan": {"type": "string", "maxLength": 1200},
        "features": {"type": "array", "items": {"anyOf": list(FEATURES.values())}, "maxItems": MAX_FEATURES},
    },
    "required": ["plan", "features"],
    "additionalProperties": False,
}

RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "cut_features", "schema": SCHEMA, "strict": True}}

SCHEMA_DOC = """\
{"type": "box", "min": [x, y, z], "max": [x, y, z]}
    axis-aligned block between two corners: pockets, steps, slots, rectangular cavities
{"type": "cylinder", "diameter": d, "start": [x, y, z], "end": [x, y, z]}
    round hole, bore, counterbore or cylindrical recess along the line start -> end (any direction)
{"type": "cone", "diameter_start": d0, "diameter_end": d1, "start": [x, y, z], "end": [x, y, z]}
    countersink, drill point or tapered bore; either diameter may be 0
{"type": "ring", "outer_diameter": D, "inner_diameter": d, "start": [x, y, z], "end": [x, y, z]}
    annular groove or the gap around a boss: removes the tube between d and D, keeps the core
{"type": "prism", "plane": "xy"|"xz"|"yz", "points": [[u, v], ...], "from": a, "to": b}
    closed polygon in that plane (for "xz" the points are (x, z)) removed from a to b along the third axis
{"type": "revolve", "axis": "x"|"y"|"z", "centre": [c1, c2], "profile": [[position, radius], ...]}
    cavity of revolution about the line parallel to `axis` through `centre` (the other two coordinates in
    x<y<z order: axis "x" -> (y, z), "y" -> (x, z), "z" -> (x, y)); profile = closed polygon of
    (position along the axis, radius) points: stepped bores, hollow turned parts
"""


class FeatureError(ValueError):
    pass


def _floats(value, count: int, what: str) -> tuple[float, ...]:
    try:
        out = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        raise FeatureError(f"{what} is not a list of numbers") from None
    if len(out) != count or not all(math.isfinite(v) for v in out):
        raise FeatureError(f"{what} needs {count} finite numbers")
    return out


def _positive(value, what: str, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FeatureError(f"{what} is not a number") from None
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise FeatureError(f"{what} must be {'>= 0' if allow_zero else '> 0'}")
    return number


def _segment(feature: dict) -> tuple[tuple[float, ...], tuple[float, ...]]:
    start, end = _floats(feature.get("start"), 3, "start"), _floats(feature.get("end"), 3, "end")
    if math.dist(start, end) < 1e-6:
        raise FeatureError("start and end are the same point")
    return start, end


def _polygon(points, what: str) -> list[tuple[float, ...]]:
    if not isinstance(points, list) or not 3 <= len(points) <= MAX_POINTS:
        raise FeatureError(f"{what} needs 3 to {MAX_POINTS} points")
    return [_floats(p, 2, f"{what} point") for p in points]


def _t(values) -> str:
    return "(" + ", ".join(f"{v:g}" for v in values) + ")"


def expression(feature: dict) -> str:
    """The cadlib expression of one feature's cutter. Raises FeatureError when the feature is unusable."""
    if not isinstance(feature, dict):
        raise FeatureError("feature is not an object")
    kind = feature.get("type")
    if kind == "box":
        lo, hi = _floats(feature.get("min"), 3, "min"), _floats(feature.get("max"), 3, "max")
        if any(abs(a - b) < 1e-6 for a, b in zip(lo, hi)):
            raise FeatureError("box has no thickness along one axis")
        return f"box({', '.join(f'{v:g}' for v in lo + hi)})"
    if kind == "cylinder":
        start, end = _segment(feature)
        return f"cyl_between({_positive(feature.get('diameter'), 'diameter'):g}, {_t(start)}, {_t(end)})"
    if kind == "cone":
        start, end = _segment(feature)
        d0 = _positive(feature.get("diameter_start"), "diameter_start", allow_zero=True)
        d1 = _positive(feature.get("diameter_end"), "diameter_end", allow_zero=True)
        if d0 == 0 and d1 == 0:
            raise FeatureError("both cone diameters are 0")
        if d0 == d1:
            return f"cyl_between({d0:g}, {_t(start)}, {_t(end)})"
        return f"cone_between({d0:g}, {d1:g}, {_t(start)}, {_t(end)})"
    if kind == "ring":
        start, end = _segment(feature)
        outer = _positive(feature.get("outer_diameter"), "outer_diameter")
        inner = _positive(feature.get("inner_diameter"), "inner_diameter")
        if inner >= outer:
            raise FeatureError("inner_diameter must be smaller than outer_diameter")
        return f"tube_between({outer:g}, {inner:g}, {_t(start)}, {_t(end)})"
    if kind == "prism":
        plane = str(feature.get("plane", "")).lower()
        if plane not in ("xy", "xz", "yz"):
            raise FeatureError("plane must be xy, xz or yz")
        a, b = _floats([feature.get("from"), feature.get("to")], 2, "from/to")
        if abs(a - b) < 1e-6:
            raise FeatureError("from and to are equal")
        points = _polygon(feature.get("points"), "points")
        return f"prism('{plane}', [{', '.join(_t(p) for p in points)}], {a:g}, {b:g})"
    if kind == "revolve":
        axis = str(feature.get("axis", "")).lower()
        if axis not in ("x", "y", "z"):
            raise FeatureError("axis must be x, y or z")
        c1, c2 = _floats(feature.get("centre"), 2, "centre")
        profile = [(a, max(r, 0.0)) for a, r in _polygon(feature.get("profile"), "profile")]
        if max(r for _, r in profile) <= 0:
            raise FeatureError("profile has no radius")
        return f"revolve('{axis}', [{', '.join(_t(p) for p in profile)}], {c1:g}, {c2:g})"
    raise FeatureError(f"unknown feature type {kind!r}")


def parse(reply: str) -> list[dict] | None:
    """The feature list of a reply; None when the reply is not the expected JSON object."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        return None
    return [f for f in features if isinstance(f, dict)][:MAX_FEATURES]


def label(feature: dict, index: int) -> str:
    text = " ".join(str(feature.get("label") or "").split())[:80]
    return f"{index + 1}. {feature.get('type', '?')}" + (f": {text}" if text else "")


def compile_script(features: list[dict], min_volume: float = 0.0, max_holes: int | None = None) -> str:
    """Script that cuts every usable feature into `base`, one guarded cut per feature."""
    lines = ["# built from the model's feature list (features.py): the model wrote no code",
             "body, feature_log = base, []"]
    limits = f"{min_volume:.1f}, {max_holes if max_holes is not None else 'None'}"
    for index, feature in enumerate(features):
        name = label(feature, index)
        try:
            cutter = expression(feature)
        except FeatureError as exc:
            lines.append(f"feature_log.append({f'{name} -> rejected: {exc}'!r})")
            continue
        lines.append(f"body = cut_feature(body, lambda: {cutter}, {name!r}, feature_log, {limits})")
    lines.append("result = body")
    return "\n".join(lines) + "\n"


def dumps(features: list[dict]) -> str:
    return "[\n" + ",\n".join("  " + json.dumps(f) for f in features) + "\n]"
