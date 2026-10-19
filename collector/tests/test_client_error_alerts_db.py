"""The hourly run opens a client_errors alert from raw events (skipped when
TimescaleDB is unreachable)."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.alerts import hourly, notifier
from app.db import pool as pool_module
from app.db import queries
from tests.test_queries_db import _db_reachable, _refresh_all

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")


def _events(service, hour, ok, unauthorized):
    return [
        queries.event_row(
            event_id=str(uuid.uuid4()), time=hour + timedelta(seconds=10 * i), service_name=service, method="GET",
            route="/orders", status_code=401 if i < unauthorized else 200, duration_ms=20.0, is_error=False,
            error_type=None, host="h",
        )
        for i in range(ok + unauthorized)
    ]


def test_hourly_run_opens_a_client_error_alert():
    service = f"test-4xx-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    hour = hourly.last_complete_hour(now)

    async def run():
        pool = await pool_module.create_pool()
        try:
            rows = _events(service, hour, ok=60, unauthorized=40)
            for week in (1, 2, 3, 4):
                rows += _events(service, hour - timedelta(weeks=week), ok=99, unauthorized=1)
            await queries.insert_events(pool, rows)
            await _refresh_all(pool)
            sent = await hourly.run_hourly_check(
                pool, [service], notifier.Channels(), timedelta(hours=6), now=now, client_error_statuses=(401, 403),
            )
            alerts = await pool.fetch("SELECT kind, route, details FROM alerts WHERE service_name = $1", service)
            return sent, [dict(a) for a in alerts]
        finally:
            await pool.execute("DELETE FROM alerts WHERE service_name = $1", service)
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool_module.close_pool()

    sent, alerts = asyncio.run(run())
    client = [a for a in alerts if a["kind"] == "client_errors"]
    assert len(client) == 1 and client[0]["route"] == "/orders"
    details = json.loads(client[0]["details"]) if isinstance(client[0]["details"], str) else client[0]["details"]
    assert (details["status_code"], details["observed_count"], details["requests"]) == (401, 40, 100)
    assert "release_context" in details
    assert sent >= 1
