"""Alert state machine and root-cause hints against TimescaleDB (skipped when
unreachable)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.alerts import notifier
from app.alerts.hourly import apply_detections
from app.db import pool as pool_module
from app.db import queries
from app.insights.hints import route_hints
from tests.test_queries_db import _db_reachable

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

RENOTIFY = timedelta(hours=6)


def _anomaly(route):
    return {"route": route, "day_of_week": "Monday", "hour_range": "08:00-09:00",
            "observed_error_rate": 0.3, "baseline_error_rate": 0.02, "observed_p95_ms": 900.0,
            "baseline_p95_ms": 300.0, "z_score": 8.0, "window_start": "2026-10-05T08:00:00+00:00"}


def test_alert_lifecycle_open_dedup_renotify_resolve():
    service = f"test-alerts-{uuid.uuid4().hex[:8]}"
    h0 = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)

    async def run():
        pool = await pool_module.create_pool()
        steps = []
        try:
            async def step(hour_offset, routes, now_offset_minutes=75):
                hour = h0 + timedelta(hours=hour_offset)
                now = hour + timedelta(minutes=now_offset_minutes)
                events = await apply_detections(
                    pool, service, hour, [_anomaly(r) for r in routes], now, RENOTIFY
                )
                steps.append(sorted((e, a["route"]) for e, a in events))

            await step(0, ["/orders"])             # opens + notifies
            await step(1, ["/orders"])             # still open, inside cooldown: silent
            await step(2, [])                      # one clean hour: not resolved yet
            await step(7, ["/orders", "/users"])   # 6h+ since notify: reminder; /users opens
            await step(8, ["/users"])              # /orders clean for 1h: stays open
            await step(9, ["/users"])              # /orders clean for 2h: resolves
            open_rows = await pool.fetch(
                "SELECT route FROM alerts WHERE service_name = $1 AND resolved_at IS NULL", service
            )
            return steps, sorted(r["route"] for r in open_rows)
        finally:
            await pool.execute("DELETE FROM alerts WHERE service_name = $1", service)
            await pool_module.close_pool()

    steps, still_open = asyncio.run(run())
    assert steps == [
        [(notifier.OPENED, "/orders")],
        [],
        [],
        [(notifier.OPENED, "/users"), (notifier.STILL_FIRING, "/orders")],
        [],
        [(notifier.RESOLVED, "/orders")],
    ]
    assert still_open == ["/users"]


def test_hints_find_the_bad_host_and_the_new_error_type():
    """Three pods share /checkout traffic. In the anomalous hour pod-3 fails
    half its requests with TimeoutError; the previous week only saw rare
    KeyErrors spread across pods."""
    service = f"test-hints-{uuid.uuid4().hex[:8]}"
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)

    def ev(ts, host, is_error, error_type=None, ms=50.0):
        return queries.event_row(
            event_id=str(uuid.uuid4()), time=ts, service_name=service, method="POST",
            route="/checkout", status_code=500 if is_error else 200, duration_ms=ms,
            is_error=is_error, error_type=error_type, host=host,
        )

    rows = []
    for i in range(600):  # previous week: 1% KeyError across all pods
        ts = hour - timedelta(days=6) + timedelta(minutes=i * 10)
        rows.append(ev(ts, f"pod-{i % 3 + 1}", i % 100 == 0, "KeyError" if i % 100 == 0 else None))
    for i in range(90):  # the anomalous hour
        ts = hour + timedelta(seconds=i * 40)
        host = f"pod-{i % 3 + 1}"
        bad = host == "pod-3" and i % 2 == 0
        rows.append(ev(ts, host, bad, "TimeoutError" if bad else None, ms=3000.0 if bad else 50.0))

    async def run():
        pool = await pool_module.create_pool()
        try:
            await queries.insert_events(pool, rows)
            async with pool.acquire() as conn:
                return await route_hints(conn, service, "/checkout", hour)
        finally:
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool_module.close_pool()

    texts = [h.text for h in asyncio.run(run())]
    assert "100% of errors came from host pod-3, which served 33% of requests" in texts
    assert "100% of slow requests came from host pod-3, which served 33% of requests" in texts
    assert "100% of errors are error type TimeoutError (not seen in the previous 7 days)" in texts
