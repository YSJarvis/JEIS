"""LLM provider interface.

Providers turn (system, user) prompts into text. Selection comes from the
environment, so keys never pass through the browser:

  VOTERENGINE_LLM_PROVIDER   auto (default) | anthropic | openai | mock
  ANTHROPIC_API_KEY / OPENAI_API_KEY
  VOTERENGINE_LLM_MODEL      overrides the provider's default model
  VOTERENGINE_LLM_TIMEOUT    seconds (default 15)
  ANTHROPIC_BASE_URL / OPENAI_BASE_URL   optional gateway or proxy endpoints

`auto` uses Anthropic if its key is set, else OpenAI, else the offline mock.
HTTP goes through the standard library (honours HTTPS_PROXY), so there are
no SDK dependencies.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Protocol


class LLMError(Exception):
    pass


class LLMProvider(Protocol):
    name: str

    def complete(self, system: str, user: str, max_tokens: int = 900) -> str: ...


def _post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise LLMError(f"HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"network error: {e}") from None


class AnthropicProvider:
    URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/") + "/v1/messages"

    def __init__(self, api_key: str, model: str | None = None, timeout: float = 15):
        self.key, self.model, self.timeout = api_key, model or "claude-haiku-5-5", timeout
        self.name = f"anthropic:{self.model}"

    def complete(self, system: str, user: str, max_tokens: int = 900) -> str:
        res = _post_json(self.URL, {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                         {"model": self.model, "max_tokens": max_tokens, "system": system,
                          "messages": [{"role": "user", "content": user}]}, self.timeout)
        try:
            return "".join(b.get("text", "") for b in res["content"] if b.get("type") == "text")
        except (KeyError, TypeError):
            raise LLMError("unexpected response shape") from None


class OpenAIProvider:
    URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"

    def __init__(self, api_key: str, model: str | None = None, timeout: float = 15):
        self.key, self.model, self.timeout = api_key, model or "gpt-4o-mini", timeout
        self.name = f"openai:{self.model}"

    def complete(self, system: str, user: str, max_tokens: int = 900) -> str:
        res = _post_json(self.URL, {"Authorization": f"Bearer {self.key}"},
                         {"model": self.model, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                         self.timeout)
        try:
            return res["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("unexpected response shape") from None


def get_provider() -> LLMProvider | None:
    """The configured live provider, or None to use the offline generator."""
    choice = os.environ.get("VOTERENGINE_LLM_PROVIDER", "auto").lower()
    model = os.environ.get("VOTERENGINE_LLM_MODEL") or None
    timeout = float(os.environ.get("VOTERENGINE_LLM_TIMEOUT", 15))
    a_key, o_key = os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("OPENAI_API_KEY")
    if choice == "mock":
        return None
    if choice in ("anthropic", "auto") and a_key:
        return AnthropicProvider(a_key, model, timeout)
    if choice in ("openai", "auto") and o_key:
        return OpenAIProvider(o_key, model, timeout)
    return None


def provider_status() -> dict:
    p = get_provider()
    return {"live": p is not None, "provider": p.name if p else "offline generator"}
