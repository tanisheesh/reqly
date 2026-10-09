"""Deploy attribution against a real TimescaleDB (skipped when unreachable)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import pool as pool_module
from app.db import queries
from app.insights.deploys import add_release_context
from tests.test_queries_db import _db_reachable

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")


def _event(service, route, ts, release, is_error, duration_ms):
    return queries.event_row(
        event_id=str(uuid.uuid4()), time=ts, service_name=service, method="POST",
        route=route, status_code=500 if is_error else 201, duration_ms=duration_ms,
        is_error=is_error, error_type=None, host="h", release=release, environment="prod",
    )


def _scenario(service, now):
    """v1 serves /orders for 10 days (2% errors, ~100ms); v2 is deployed 2
    days ago and /orders gets worse (20% errors, ~400ms)."""
    deploy = (now - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    rows = []
    t = deploy - timedelta(days=10)
    i = 0
    while t < now - timedelta(minutes=5):
        on_v2 = t >= deploy
        rows.append(_event(
            service, "/orders", t, "v2" if on_v2 else "v1",
            is_error=(i % 5 == 0) if on_v2 else (i % 50 == 0),
            duration_ms=400.0 if on_v2 else 100.0,
        ))
        t += timedelta(minutes=10)
        i += 1
    return deploy, rows


def test_anomaly_on_new_release_gets_before_after_context():
    service = f"test-deploy-ctx-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    async def run():
        pool = await pool_module.create_pool()
        try:
            deploy, rows = _scenario(service, now)
            await queries.insert_events(pool, rows)
            await queries.record_deployments(pool, rows)
            anomalies = [
                # an hour on v2, a day after the deploy
                {"route": "/orders", "window_start": (deploy + timedelta(days=1)).isoformat()},
                # an hour on v1 the day before the deploy: v1 was first seen
                # 9 days earlier, so it isn't "new" for that hour
                {"route": "/orders", "window_start": (deploy - timedelta(days=1)).isoformat()},
                # a route with no traffic in that hour
                {"route": "/nothing", "window_start": (deploy + timedelta(days=1)).isoformat()},
            ]
            await add_release_context(pool, service, anomalies, now=now)
            return deploy, anomalies
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM request_events WHERE service_name = $1", service)
                await conn.execute("DELETE FROM deployments WHERE service_name = $1", service)
            await pool_module.close_pool()

    deploy, (on_v2, on_v1, empty) = asyncio.run(run())

    ctx = on_v2["release_context"]
    assert ctx["release"] == "v2"
    assert ctx["is_new_release"] is True
    assert datetime.fromisoformat(ctx["release_first_seen_at"]) == deploy
    assert ctx["previous_release"] == "v1"
    assert ctx["before"]["error_rate"] == pytest.approx(0.02, abs=0.01)
    assert ctx["after"]["error_rate"] == pytest.approx(0.20, abs=0.02)
    assert ctx["before"]["p95_ms"] == pytest.approx(100.0)
    assert ctx["after"]["p95_ms"] == pytest.approx(400.0)

    assert on_v1["release_context"]["release"] == "v1"
    assert on_v1["release_context"]["is_new_release"] is False
    assert "before" not in on_v1["release_context"]

    assert empty["release_context"] is None


def test_release_list_and_chart_markers():
    service = f"test-releases-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    async def run():
        pool = await pool_module.create_pool()
        try:
            deploy, rows = _scenario(service, now)
            await queries.insert_events(pool, rows)
            await queries.record_deployments(pool, rows)
            releases = await queries.list_releases(pool, service)
            markers_7d = await queries.get_release_markers(pool, service, "7d")
            markers_1h = await queries.get_release_markers(pool, service, "1h")
            return deploy, releases, markers_7d, markers_1h
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM request_events WHERE service_name = $1", service)
                await conn.execute("DELETE FROM deployments WHERE service_name = $1", service)
            await pool_module.close_pool()

    deploy, releases, markers_7d, markers_1h = asyncio.run(run())

    assert [r["release"] for r in releases] == ["v2", "v1"]  # newest first
    v2, v1 = releases
    assert v2["first_seen_at"] == deploy
    assert v2["environments"] == ["prod"]
    assert v2["error_rate"] == pytest.approx(0.20, abs=0.02)
    assert v1["error_rate"] == pytest.approx(0.02, abs=0.01)
    assert v2["p95_ms"] == pytest.approx(400.0)
    assert v1["request_count"] > v2["request_count"] > 0

    assert [m["release"] for m in markers_7d] == ["v2"]  # v1 started 12 days ago
    assert markers_1h == []
