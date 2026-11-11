"""Minimal OpenAI-compatible chat client (works with vLLM and hosted endpoints)."""

from __future__ import annotations

import time

import requests

from agentic_cad.config import Settings


class LLMError(RuntimeError):
    pass


class ChatClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        self._model = settings.model

    @property
    def model(self) -> str:
        """OPENAI_MODEL, or the first model the endpoint serves when it is not set."""
        if not self._model:
            try:
                response = self.session.get(f"{self.settings.base_url}/models", headers=self._headers(), timeout=15)
                response.raise_for_status()
                self._model = response.json()["data"][0]["id"]
            except Exception as exc:
                raise LLMError(f"OPENAI_MODEL is not set and {self.settings.base_url}/models failed: {exc}") from exc
        return self._model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}

    def chat(self, messages: list[dict], timeout: float, temperature: float | None = None) -> tuple[str, dict]:
        """Returns (reply text, usage). Retries transient failures while time allows."""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.settings.max_tokens,
            "temperature": self.settings.temperature if temperature is None else temperature,
        }
        deadline = time.time() + timeout
        last: Exception | None = None
        for attempt in range(3):
            remaining = deadline - time.time()
            if remaining < 5:
                break
            try:
                response = self.session.post(f"{self.settings.base_url}/chat/completions", json=payload,
                                             headers=self._headers(), timeout=(10, remaining))
                if response.status_code >= 500 or response.status_code == 429:
                    raise LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
                if response.status_code >= 400:   # bad request: retrying the same payload cannot help
                    raise LLMError(f"HTTP {response.status_code}: {response.text[:500]}")
                data = response.json()
                message = data["choices"][0]["message"]
                text = message.get("content") or ""
                if isinstance(text, list):
                    text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
                return text, data.get("usage") or {}
            except LLMError as exc:
                last = exc
                if "HTTP 4" in str(exc) and "HTTP 429" not in str(exc):
                    break
            except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
                last = LLMError(f"{type(exc).__name__}: {exc}")
            time.sleep(min(2.0 * (attempt + 1), max(0.0, deadline - time.time())))
        raise last or LLMError("no time left for the LLM call")
