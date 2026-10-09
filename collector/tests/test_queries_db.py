"""Integration tests that run the real SQL against TimescaleDB.

CI provides a TimescaleDB service container (see .github/workflows/ci.yml).
Locally these are skipped unless DATABASE_URL points at a reachable instance.
pytest-asyncio isn't a CI dependency, so each test drives its own event loop.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.config import settings
from app.db import pool as pool_module
from app.db import queries


def _db_reachable() -> bool:
    async def probe():
        conn = await asyncpg.connect(settings.database_url, timeout=3)
        await conn.close()

    try:
        asyncio.run(probe())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")


def _row(service, route, ts, is_error, **extra):
    return queries.event_row(
        event_id=str(uuid.uuid4()), time=ts, service_name=service, method="GET",
        route=route, status_code=500 if is_error else 200, duration_ms=10.0,
        is_error=is_error, error_type=None, host="test-host", **extra,
    )


def test_top_routes_error_rate_is_not_weighted_by_active_minutes():
    """Hour A: 60 requests spread over 60 minutes, no errors.
    Hour B: 60 requests packed into a single minute, all errors.
    True error rate is 60/120 = 50%. Joining 1-minute rows to hourly rows
    before aggregating would repeat hour A's error row 60 times and report
    ~1.6% instead.
    """
    service = f"test-svc-{uuid.uuid4().hex[:8]}"

    async def run():
        pool = await pool_module.create_pool()
        try:
            hour_a = datetime.now(timezone.utc).replace(
                minute=0, second=0, microsecond=0
            ) - timedelta(hours=5)
            hour_b = hour_a + timedelta(hours=1)
            rows = [_row(service, "/r", hour_a + timedelta(minutes=m), False) for m in range(60)]
            rows += [_row(service, "/r", hour_b + timedelta(seconds=s), True) for s in range(60)]
            await queries.insert_events(pool, rows)

            async with pool.acquire() as conn:
                for view in ("route_latency_1min", "route_errors_1hour"):
                    await conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")

            return await queries.get_top_routes(pool, service, "24h")
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool_module.close_pool()

    top = asyncio.run(run())
    assert len(top) == 1
    assert top[0]["route"] == "/r"
    assert top[0]["request_count"] == 120
    assert top[0]["error_rate"] == pytest.approx(0.5)


def test_late_events_reach_the_hourly_aggregate_after_refresh():
    """Events 10 days old are outside every refresh policy's look-back, so
    they only reach route_errors_1hour through the late-data refresher."""
    from app.db.late_data import LateDataTracker

    service = f"test-late-{uuid.uuid4().hex[:8]}"

    async def run():
        pool = await pool_module.create_pool()
        try:
            hour = datetime.now(timezone.utc).replace(
                minute=0, second=0, microsecond=0
            ) - timedelta(days=10)
            rows = [_row(service, "/late", hour + timedelta(minutes=m), m % 4 == 0) for m in range(40)]
            await queries.insert_events(pool, rows)

            count_sql = (
                "SELECT coalesce(sum(request_count), 0) FROM route_errors_1hour "
                "WHERE service_name = $1"
            )
            before = await pool.fetchval(count_sql, service)

            tracker = LateDataTracker()
            tracker.note(min(r[1] for r in rows))
            calls = await tracker.refresh(pool)

            after = await pool.fetchval(count_sql, service)
            return before, calls, after, tracker.pending_since
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool_module.close_pool()

    before, calls, after, pending = asyncio.run(run())
    assert before == 0
    assert calls > 0
    assert after == 40
    assert pending is None


def test_record_deployments_tracks_first_and_last_seen_per_release():
    service = f"test-deploy-{uuid.uuid4().hex[:8]}"

    async def run():
        pool = await pool_module.create_pool()
        try:
            t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=3)
            batch_1 = [
                _row(service, "/r", t0 + timedelta(minutes=m), False, release="v1", environment="prod")
                for m in (10, 0, 20)
            ]
            batch_2 = [
                _row(service, "/r", t0 + timedelta(minutes=90), False, release="v1", environment="prod"),
                _row(service, "/r", t0 + timedelta(minutes=95), False, release="v2", environment="prod"),
                _row(service, "/r", t0 + timedelta(minutes=96), False),  # no release: ignored
            ]
            for batch in (batch_1, batch_2):
                await queries.insert_events(pool, batch)
                await queries.record_deployments(pool, batch)
            rows = await pool.fetch(
                "SELECT environment, release, first_seen_at, last_seen_at FROM deployments "
                "WHERE service_name = $1 ORDER BY release",
                service,
            )
            stored_release = await pool.fetchval(
                "SELECT release FROM request_events WHERE service_name = $1 AND release = 'v2'",
                service,
            )
            return t0, [dict(r) for r in rows], stored_release
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM request_events WHERE service_name = $1", service)
                await conn.execute("DELETE FROM deployments WHERE service_name = $1", service)
            await pool_module.close_pool()

    t0, rows, stored_release = asyncio.run(run())
    assert stored_release == "v2"
    assert rows == [
        {"environment": "prod", "release": "v1",
         "first_seen_at": t0, "last_seen_at": t0 + timedelta(minutes=90)},
        {"environment": "prod", "release": "v2",
         "first_seen_at": t0 + timedelta(minutes=95), "last_seen_at": t0 + timedelta(minutes=95)},
    ]
