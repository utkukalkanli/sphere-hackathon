"""Agent settings. Everything is read from the environment so the image is configured by ENV lines
in the Dockerfile and by the handed-over <team>.env (README: Configuration)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name, "")
    return default if value == "" else value.strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    max_iters: int          # LLM attempts per test case
    workers: int            # test cases processed concurrently
    time_budget_s: float    # wall-clock budget for the whole run (hard limit is 20 min)
    llm_timeout_s: float
    exec_timeout_s: float   # one generated script: build + export + render
    max_tokens: int
    temperature: float
    use_images: bool        # send the reference views to the model (needs a vision model)
    feedback_images: bool   # send target-vs-candidate comparison images with each correction
    prompt_image_px: int    # reference views are downscaled to this before being sent
    mask_px: int            # resolution of the silhouette comparison
    tile_px: int            # tile size in the comparison images
    use_facts: bool         # flow engine: read facts.json when the case has one (hidden parts have none)
    engine: str             # "hull" (silhouette hull + facts + LLM carving), "flow" (fixed LangGraph nodes, works
                            # from the brief alone), "graph" (LangGraph tool-calling
                            # agent) or "legacy" (fixed generate/correct loop)
    llm_stage: bool         # hull engine: let the model carve the hull (off = deterministic stages only)
    candidates: int         # hull engine: scripts requested in parallel per round
    patience: int           # hull engine: rounds without an improvement before the case stops
    carve_mode: str         # hull engine: "script" (the model writes CadQuery) or "features" (the model fills in
                            # the JSON schema of features.py and the geometry is built from it)
    carve_images: bool      # hull engine, feature mode: send the reference views with the request (vision model)
    carve_min_excess: float  # hull engine: the model only carves when the base is this much too heavy (1.0 = +100%)
    vlm_base_url: str       # vision model for the graph engine; empty = text only
    vlm_api_key: str
    vlm_model: str
    max_steps: int          # graph engine: LLM turns per test case
    enable_thinking: bool   # Qwen3-style reasoning (chat_template_kwargs.enable_thinking)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_url=(os.environ.get("OPENAI_BASE_URL") or "http://localhost:8000/v1").rstrip("/"),
            api_key=os.environ.get("OPENAI_API_KEY") or "EMPTY",
            model=os.environ.get("OPENAI_MODEL") or "",
            max_iters=_int("AGENT_MAX_ITERS", 6),
            workers=_int("AGENT_WORKERS", 9),
            time_budget_s=_float("AGENT_TIME_BUDGET_S", 17 * 60),
            llm_timeout_s=_float("AGENT_LLM_TIMEOUT_S", 240),
            exec_timeout_s=_float("AGENT_EXEC_TIMEOUT_S", 90),
            max_tokens=_int("AGENT_MAX_TOKENS", 6000),
            temperature=_float("AGENT_TEMPERATURE", 0.2),
            use_images=_flag("AGENT_USE_IMAGES", True),
            feedback_images=_flag("AGENT_FEEDBACK_IMAGES", True),
            prompt_image_px=_int("AGENT_PROMPT_IMAGE_PX", 448),
            mask_px=_int("AGENT_MASK_PX", 512),
            tile_px=_int("AGENT_TILE_PX", 320),
            use_facts=_flag("AGENT_USE_FACTS", True),
            engine=(os.environ.get("AGENT_ENGINE") or "hull").strip().lower(),
            llm_stage=_flag("AGENT_LLM_STAGE", True),
            candidates=_int("AGENT_CANDIDATES", 4),
            patience=_int("AGENT_PATIENCE", 4),
            carve_mode=(os.environ.get("AGENT_CARVE_MODE") or "script").strip().lower(),
            carve_images=_flag("AGENT_CARVE_IMAGES", False),
            carve_min_excess=_float("AGENT_CARVE_MIN_EXCESS", 1.0),
            vlm_base_url=(os.environ.get("VLM_BASE_URL") or "").rstrip("/"),
            vlm_api_key=os.environ.get("VLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "EMPTY",
            vlm_model=os.environ.get("VLM_MODEL") or "",
            max_steps=_int("AGENT_MAX_STEPS", 16),
            enable_thinking=_flag("AGENT_ENABLE_THINKING", False),
        )
