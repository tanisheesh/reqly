"""Which OpenAI-compatible LLM the collector talks to, and one report call
through the real client against a stand-in server."""

import asyncio
import dataclasses
import json

import httpx
import pytest
from openai import AsyncOpenAI

from app import config
from app.insights import llm_report

_LLM_VARS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "GROQ_API_KEY", "GROQ_MODEL")


@pytest.fixture
def env(monkeypatch):
    for name in _LLM_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_nothing_set_means_no_llm(env):
    assert config._llm_from_env()[0] is None


def test_groq_key_alone_keeps_working(env):
    env.setenv("GROQ_API_KEY", "gsk")
    env.setenv("GROQ_MODEL", "llama-x")
    assert config._llm_from_env() == (config.GROQ_BASE_URL, "gsk", "llama-x")


def test_llm_api_key_alone_means_openai(env):
    env.setenv("LLM_API_KEY", "sk")
    env.setenv("LLM_MODEL", "gpt-4.1-mini")
    assert config._llm_from_env() == (config.OPENAI_BASE_URL, "sk", "gpt-4.1-mini")


def test_a_local_server_needs_no_key_and_never_gets_the_groq_key(env):
    env.setenv("LLM_BASE_URL", "http://localhost:11434/v1/")
    env.setenv("LLM_MODEL", "qwen3:8b")
    env.setenv("GROQ_API_KEY", "gsk")
    assert config._llm_from_env() == ("http://localhost:11434/v1", None, "qwen3:8b")


def test_report_goes_through_the_chat_completions_api(monkeypatch):
    seen = []

    def server(request):
        seen.append(request)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "- p95 doubled on /orders"}}],
        })

    client = AsyncOpenAI(base_url="http://llm.test/v1", api_key="k",
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(server)))
    monkeypatch.setattr(llm_report, "llm_client", lambda: client)
    monkeypatch.setattr(llm_report, "settings", dataclasses.replace(
        llm_report.settings, llm_base_url="http://llm.test/v1", llm_model="some-model"))

    text = asyncio.run(llm_report.generate_report("shop", "2026-10-05", [{"route": "/orders"}]))
    assert text == "- p95 doubled on /orders"
    assert str(seen[0].url) == "http://llm.test/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer k"
    assert json.loads(seen[0].content)["model"] == "some-model"
