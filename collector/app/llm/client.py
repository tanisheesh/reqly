"""The model behind the weekly reports and Ask Reqly: any OpenAI-compatible
chat completions API (Groq, OpenAI, OpenRouter, Ollama, vLLM, ...), set by
LLM_BASE_URL / LLM_API_KEY / LLM_MODEL. See config.py for the fallbacks."""

from __future__ import annotations

from functools import lru_cache

from ..config import settings

_TIMEOUT_SECONDS = 30.0


@lru_cache(maxsize=1)
def _cached(base_url: str, api_key: str | None):
    from openai import AsyncOpenAI

    # A local server (Ollama) needs no key, but the client insists on one.
    return AsyncOpenAI(base_url=base_url, api_key=api_key or "unused", timeout=_TIMEOUT_SECONDS, max_retries=1)


def llm_client():
    """The shared async client. Callers check settings.llm_enabled first."""
    if not settings.llm_base_url:
        raise RuntimeError("no LLM configured (set LLM_BASE_URL or GROQ_API_KEY)")
    return _cached(settings.llm_base_url, settings.llm_api_key)
