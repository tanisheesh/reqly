"""Ask Reqly: the tool-calling loop (with a scripted fake model), argument
validation and the endpoint's guards. No database or API key needed."""

import asyncio
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ask import agent, tools
from app.config import settings
from app.main import app
from app.routers import ask as ask_router

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _call(name, arguments, call_id="c1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def _response(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeModel:
    """Returns the scripted responses in order and records each request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def fake_db(monkeypatch):
    """Stubs the agent's DB reads; returns the list of tool calls it ran."""
    ran = []

    async def list_routes(pool, service):
        return ["/orders", "/orders/{id}"]

    async def sketches_available(pool):
        return True

    async def recent_releases(pool, service):
        return [{"release": "v2", "first_seen_at": NOW - timedelta(hours=36)}]

    async def run_tool(pool, service, name, args, now):
        ran.append((name, args))
        return {"stats": {"requests": 1200, "error_rate": 0.091}}

    monkeypatch.setattr(agent.queries, "list_routes", list_routes)
    monkeypatch.setattr(agent.queries, "sketches_available", sketches_available)
    monkeypatch.setattr(agent, "_recent_releases", recent_releases)
    monkeypatch.setattr(agent, "run_tool", run_tool)
    return ran


def test_tool_call_then_answer(fake_db):
    model = FakeModel(
        _response(tool_calls=[_call("get_stats", json.dumps({"route": "/orders", "start": "2026-10-09T00:00Z"}))]),
        _response(content="/orders failed 9.1% of 1,200 requests since yesterday."),
    )
    result = asyncio.run(agent.ask(None, "flask-demo", "how is /orders doing?", NOW, client=model))

    assert result["answer"].startswith("/orders failed 9.1%")
    assert fake_db == [("get_stats", {"route": "/orders", "start": "2026-10-09T00:00Z"})]
    assert result["steps"][0]["tool"] == "get_stats"
    assert result["steps"][0]["result"]["stats"]["requests"] == 1200

    system = model.requests[0]["messages"][0]["content"]
    assert '"flask-demo"' in system and "2026-10-10 12:00" in system and "/orders/{id}" in system
    assert "v2 (2026-10-09 00:00)" in system
    assert "- Saturday: 2026-10-10 (today)\n- Friday: 2026-10-09 (yesterday)\n" in system
    assert "- Monday: 2026-10-05\n- Sunday: 2026-10-04\n- Saturday: 2026-10-03\n" in system
    # the second request carries the assistant's tool call and the tool's answer
    second = model.requests[1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "c1"
    assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": json.dumps(result["steps"][0]["result"])}


def test_bad_arguments_go_back_to_the_model(fake_db):
    model = FakeModel(
        _response(tool_calls=[_call("get_stats", "{not json")]),
        _response(content="Sorry, I couldn't read that."),
    )
    result = asyncio.run(agent.ask(None, "svc", "?", NOW, client=model))
    assert fake_db == []  # never ran
    assert result["steps"][0]["result"] == {"error": "arguments were not valid JSON"}
    assert json.loads(model.requests[1]["messages"][-1]["content"]) == {"error": "arguments were not valid JSON"}


def test_tool_calls_are_capped(fake_db):
    greedy = [
        _response(tool_calls=[_call("get_stats", "{}", f"c{i}a"), _call("get_slos", "{}", f"c{i}b")])
        for i in range(agent.MAX_ROUNDS)
    ]
    model = FakeModel(*greedy[:3], _response(content="Here is what I found."))
    result = asyncio.run(agent.ask(None, "svc", "?", NOW, client=model))

    assert len(fake_db) == agent.MAX_TOOL_CALLS
    assert result["answer"] == "Here is what I found."
    # once the cap is hit, the model gets no tools, and the tool history is
    # handed over as plain text
    final = model.requests[-1]
    assert "tools" not in final
    assert [m["role"] for m in final["messages"]] == ["system", "user", "user"]
    assert final["messages"][-1]["content"].count("get_stats({}) -> ") == 3
    assert final["messages"][-1]["content"].endswith(agent.FINAL_ANSWER_NUDGE)


def test_provider_failure_is_an_ask_error(fake_db):
    model = FakeModel(RuntimeError("503 service unavailable"))
    with pytest.raises(agent.AskError):
        asyncio.run(agent.ask(None, "svc", "?", NOW, client=model))
    assert len(model.requests) == 1


def test_malformed_tool_call_is_retried_once(fake_db):
    model = FakeModel(RuntimeError("400 tool_use_failed"), _response(content="Answer."))
    assert asyncio.run(agent.ask(None, "svc", "?", NOW, client=model))["answer"] == "Answer."

    model = FakeModel(RuntimeError("400 tool_use_failed"), RuntimeError("400 tool_use_failed"))
    with pytest.raises(agent.AskError):
        asyncio.run(agent.ask(None, "svc", "?", NOW, client=model))


def test_time_range_defaults_and_clamps():
    start, end = tools.time_range({}, NOW, tools.RAW_RETENTION)
    assert (start, end) == (NOW - timedelta(hours=24), NOW)

    # naive timestamps are UTC; the future is clamped to now; the past to retention
    start, end = tools.time_range(
        {"start": "2025-01-01T00:00:00", "end": "2027-01-01T00:00:00Z"}, NOW, tools.RAW_RETENTION
    )
    assert (start, end) == (NOW - tools.RAW_RETENTION, NOW)

    with pytest.raises(tools.ToolError, match="ISO 8601"):
        tools.time_range({"start": "yesterday"}, NOW, tools.RAW_RETENTION)
    with pytest.raises(tools.ToolError, match="empty time range"):
        tools.time_range({"start": "2026-01-01T00:00Z", "end": "2026-01-02T00:00Z"}, NOW, tools.RAW_RETENTION)


def test_run_tool_rejects_unknown_tools_and_bad_dimensions():
    unknown = asyncio.run(tools.run_tool(None, "svc", "drop_table", {}, NOW))
    assert "unknown tool" in unknown["error"]
    bad = asyncio.run(tools.run_tool(None, "svc", "get_breakdown", {"dimension": "password"}, NOW))
    assert "dimension must be one of" in bad["error"]


def test_answers_use_plain_hyphens_and_spaces(fake_db):
    model = FakeModel(_response(content=" v2 didn\u2019t go out 2026\u201110\u201108 at 14:00\u202fUTC. "))
    assert asyncio.run(agent.ask(None, "svc", "?", NOW, client=model))["answer"] == "v2 didn't go out 2026-10-08 at 14:00 UTC."


def test_numbers_are_rounded_for_the_model():
    assert tools._num(0.0912345678) == 0.09123
    assert tools._num(1234.0) == 1234
    assert tools._num(None) is None
    assert tools._num(float("nan")) is None


def test_every_tool_has_a_schema():
    assert {s["function"]["name"] for s in tools.TOOL_SCHEMAS} == set(tools.TOOLS)


@pytest.fixture
def api(monkeypatch):
    async def list_services(pool):
        return ["flask-demo"]

    monkeypatch.setattr(ask_router, "get_pool", lambda: None)
    monkeypatch.setattr(ask_router.queries, "list_services", list_services)
    monkeypatch.setattr(ask_router, "_budget", ask_router._DailyBudget())
    ask_router.limiter.reset()
    return TestClient(app)


def _with_settings(monkeypatch, **changes):
    monkeypatch.setattr(ask_router, "settings", dataclasses.replace(settings, **changes))


def _post(api, **body):
    return api.post(
        "/v1/ask",
        json={"service_name": "flask-demo", "question": "why is /orders failing?", **body},
        headers={"X-Reqly-Key": settings.read_key},
    )


def test_ask_needs_a_groq_key(api, monkeypatch):
    _with_settings(monkeypatch, groq_api_key=None)
    assert _post(api).status_code == 503


def test_ask_rejects_unknown_service_and_bad_keys(api, monkeypatch):
    _with_settings(monkeypatch, groq_api_key="k")
    assert _post(api, service_name="nope").status_code == 404
    assert api.post("/v1/ask", json={"service_name": "flask-demo", "question": "hi?"}).status_code == 401
    # the ingest key is not the read key
    if settings.ingest_key != settings.read_key:
        response = api.post(
            "/v1/ask", json={"service_name": "flask-demo", "question": "hi?"},
            headers={"X-Reqly-Key": settings.ingest_key},
        )
        assert response.status_code == 401


def test_ask_daily_limit_and_model_errors(api, monkeypatch):
    _with_settings(monkeypatch, groq_api_key="k", ask_daily_limit=2)
    answers = iter([{"answer": "ok", "steps": [], "model": "m"}, agent.AskError("model broke")])

    async def fake_ask(pool, service, question, now):
        item = next(answers)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(ask_router, "ask", fake_ask)
    assert _post(api).json()["answer"] == "ok"
    failed = _post(api)
    assert failed.status_code == 502 and failed.json()["detail"] == "model broke"
    assert _post(api).status_code == 429  # 2 per day used up
