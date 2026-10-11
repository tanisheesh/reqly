from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.insights.scheduler import _current_week_start
from app.main import app
from app.routers import insights as insights_router


@pytest.fixture
def api(monkeypatch):
    state = {"latest": None, "generated": 0}

    async def latest(pool, service):
        return state["latest"]

    async def generate(service):
        state["generated"] += 1
        return {"service_name": service, "report_text": "fresh"}

    monkeypatch.setattr(insights_router, "get_pool", lambda: None)
    monkeypatch.setattr(insights_router.queries, "get_latest_insight_report", latest)
    monkeypatch.setattr(insights_router.queries, "list_services", lambda pool: _services())
    monkeypatch.setattr(insights_router, "run_insights_for_service", generate)
    insights_router.limiter.reset()
    client = TestClient(app)
    client.state = state
    return client


async def _services():
    return ["svc"]


def _report(generated_at, week_start=None):
    return {
        "service_name": "svc", "week_start": week_start or _current_week_start(),
        "anomalies_json": "[]", "report_text": "cached text", "generated_at": generated_at,
    }


def _generate(api):
    return api.post("/v1/insights/generate?service_name=svc", headers={"X-Reqly-Key": settings.read_key})


def test_recent_report_is_returned_without_calling_the_llm(api):
    api.state["latest"] = _report(datetime.now(timezone.utc) - timedelta(minutes=3))
    body = _generate(api).json()
    assert body["report_text"] == "cached text" and body["cached"] is True
    assert api.state["generated"] == 0


def test_older_or_last_weeks_report_is_regenerated(api):
    api.state["latest"] = _report(datetime.now(timezone.utc) - timedelta(minutes=30))
    assert _generate(api).json()["report_text"] == "fresh"
    api.state["latest"] = _report(datetime.now(timezone.utc), week_start=_current_week_start() - timedelta(days=7))
    assert _generate(api).json()["report_text"] == "fresh"
    api.state["latest"] = None
    assert _generate(api).json()["report_text"] == "fresh"
    assert api.state["generated"] == 3


def test_unknown_service_is_404_and_nothing_is_generated(api):
    r = api.post("/v1/insights/generate?service_name=made-up", headers={"X-Reqly-Key": settings.read_key})
    assert r.status_code == 404
    assert api.state["generated"] == 0
