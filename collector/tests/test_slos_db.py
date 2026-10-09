"""SLO status and SLO alerts against TimescaleDB (skipped when unreachable)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.alerts import notifier
from app.alerts.slo_alerts import apply_slo_status
from app.db import pool as pool_module
from app.db import queries
from app.slo import status as slo_status
from app.slo.model import FAST_BURN, OK
from tests.test_queries_db import _db_reachable, _refresh_all, aggregate_mode  # noqa: F401

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")


def _rows(service, now):
    """/orders over the last 3 days: 1% errors and 100ms, except the last
    hour, where 30% fail and take 900ms."""
    rows = []
    t = now - timedelta(days=3)
    i = 0
    while t < now - timedelta(seconds=10):
        recent = t >= now - timedelta(hours=1)
        bad = (i % 10 < 3) if recent else (i % 100 == 0)
        rows.append(queries.event_row(
            event_id=str(uuid.uuid4()), time=t, service_name=service, method="POST",
            route="/orders", status_code=500 if bad else 201,
            duration_ms=900.0 if recent and bad else 100.0, is_error=bad, error_type=None, host="h",
        ))
        t += timedelta(seconds=30 if recent else 120)
        i += 1
    return rows


def test_slo_status_from_real_events(aggregate_mode):  # noqa: F811
    service = f"test-slo-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    async def run():
        pool = await pool_module.create_pool()
        try:
            await queries.insert_events(pool, _rows(service, now))
            await _refresh_all(pool)
            availability = await slo_status.upsert_slo(pool, {
                "service_name": service, "name": "orders availability", "route": "/orders",
                "objective": "availability", "target": 0.99, "window_days": 28,
            })
            latency = await slo_status.upsert_slo(pool, {
                "service_name": service, "name": "latency", "route": None,
                "objective": "latency", "target": 0.95, "latency_threshold_ms": 500, "window_days": 7,
            })
            # upsert by name updates in place
            again = await slo_status.upsert_slo(pool, {**availability, "target": 0.995})
            assert again["id"] == availability["id"] and again["target"] == 0.995
            return (
                await slo_status.slo_status(pool, again, now),
                await slo_status.slo_status(pool, latency, now),
                await slo_status.list_slos(pool, service),
            )
        finally:
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool.execute("DELETE FROM slos WHERE service_name = $1", service)
            await pool_module.close_pool()

    avail, lat, listed = asyncio.run(run())
    assert [s["name"] for s in listed] == ["latency", "orders availability"]

    # last hour: 30% errors on a 99.5% SLO -> 60x burn: fast burn
    assert avail["state"] == FAST_BURN
    assert avail["burn_rates"]["1h"] == pytest.approx(60, rel=0.1)
    assert avail["burn_rates"]["5m"] > 14.4
    assert avail["sli"] == pytest.approx(1 - avail["bad"] / avail["total"], abs=1e-6)
    assert avail["budget_remaining"] < 0.5

    # latency: only the slow errors of the last hour exceed 500ms
    assert lat["total"] == avail["total"]
    assert lat["bad"] == pytest.approx(36, abs=6)  # 360 recent requests x 30% x 1/3 slow-and-bad share
    assert lat["burn_rates"]["1h"] == pytest.approx(0.3 / 0.05, rel=0.1)


def test_slo_alert_lifecycle():
    service = f"test-slo-alerts-{uuid.uuid4().hex[:8]}"
    slo = {"id": 1, "service_name": service, "name": "orders availability", "route": "/orders",
           "objective": "availability", "target": 0.99, "latency_threshold_ms": None, "window_days": 28}
    burning = {"state": FAST_BURN, "sli": 0.97, "budget_remaining": 0.4,
               "burn_rates": {"5m": 30.0, "30m": 25.0, "1h": 20.0, "6h": 9.0}}
    healthy = {**burning, "state": OK, "burn_rates": {"5m": 0.5, "30m": 0.5, "1h": 0.5, "6h": 0.5}}
    t0 = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)

    async def run():
        pool = await pool_module.create_pool()
        events = []
        try:
            for minutes, status in [(0, burning), (5, burning), (10, healthy), (35, healthy),
                                    (45, healthy), (400, burning)]:
                event = await apply_slo_status(
                    pool, slo, status, t0 + timedelta(minutes=minutes), timedelta(hours=6)
                )
                events.append(event[0] if event else None)
            return events
        finally:
            await pool.execute("DELETE FROM alerts WHERE service_name = $1", service)
            await pool_module.close_pool()

    assert asyncio.run(run()) == [
        notifier.OPENED,     # starts burning
        None,                # still burning, inside the reminder interval
        None,                # recovered 5 minutes ago: not resolved yet
        notifier.RESOLVED,   # 30 minutes without burning
        None,                # nothing open, nothing burning
        notifier.OPENED,     # burns again later: a new alert
    ]
