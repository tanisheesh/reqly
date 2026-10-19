"""Two collectors on one database: each scheduled job runs once per slot
(skipped when TimescaleDB is unreachable)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.alerts import hourly, slo_alerts
from app.db import pool as pool_module
from app.insights import scheduler
from tests.test_queries_db import _db_reachable

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")


def test_a_slot_is_claimed_once_and_old_claims_are_pruned():
    slot = datetime(2001, 1, 1, 10, tzinfo=timezone.utc)  # far from any real run

    async def run():
        pool = await pool_module.create_pool()
        try:
            await pool.execute("DELETE FROM scheduled_runs WHERE job = 'test-job'")
            first = await asyncio.gather(*(scheduler.claim_run(pool, "test-job", slot) for _ in range(5)))
            again = await scheduler.claim_run(pool, "test-job", slot)
            later = await scheduler.claim_run(pool, "test-job", slot + timedelta(days=40))  # prunes the old one
            left = await pool.fetch("SELECT slot FROM scheduled_runs WHERE job = 'test-job'")
            return first, again, later, [r["slot"] for r in left]
        finally:
            await pool.execute("DELETE FROM scheduled_runs WHERE job = 'test-job'")
            await pool_module.close_pool()

    first, again, later, left = asyncio.run(run())
    assert sorted(first) == [False] * 4 + [True]  # concurrent claims: exactly one wins
    assert again is False and later is True
    assert left == [slot + timedelta(days=40)]


def test_two_collectors_run_each_alert_job_once(monkeypatch):
    calls = {"hourly": 0, "slo": 0}

    async def fake_hourly(pool, services, channels, renotify_after, now=None, **kwargs):
        calls["hourly"] += 1
        await asyncio.sleep(0.05)

    async def fake_slo(pool, channels, renotify_after, now=None):
        calls["slo"] += 1

    monkeypatch.setattr(hourly, "run_hourly_check", fake_hourly)
    monkeypatch.setattr(slo_alerts, "run_slo_check", fake_slo)

    async def run():
        await pool_module.create_pool()
        pool = pool_module.get_pool()
        now = datetime.now(timezone.utc)
        slots = [hourly.last_complete_hour(now), scheduler._five_minutes(now)]
        try:
            await pool.execute("DELETE FROM scheduled_runs WHERE slot = ANY($1::timestamptz[])", slots)
            # two collectors firing the same jobs at the same moment
            await asyncio.gather(scheduler.run_hourly_alerts(), scheduler.run_hourly_alerts())
            await asyncio.gather(scheduler.run_slo_alerts(), scheduler.run_slo_alerts())
            await scheduler.run_hourly_alerts()  # and one a little late
        finally:
            await pool.execute("DELETE FROM scheduled_runs WHERE slot = ANY($1::timestamptz[])", slots)
            await pool_module.close_pool()

    asyncio.run(run())
    assert calls == {"hourly": 1, "slo": 1}
