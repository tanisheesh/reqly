"""LLM cost alerts end to end against TimescaleDB (skipped when unreachable):
events -> llm_usage_1hour -> detection -> alerts table, next to anomaly alerts."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.alerts import hourly, llm_cost, notifier
from app.db import pool as pool_module
from app.db import queries
from tests.test_queries_db import _db_reachable

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

HOUR = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
CHANNELS = notifier.Channels()  # nothing configured: alerts are stored, nothing is sent
RENOTIFY = timedelta(hours=6)


def _hour_rows(service, hour, tokens_in, calls=20):
    rows = []
    for i in range(calls):
        rows.append(queries.event_row(
            event_id=str(uuid.uuid4()), time=hour + timedelta(minutes=i), service_name=service,
            method="POST", route="/chat", status_code=200, duration_ms=800.0, is_error=False,
            error_type=None, host="h", llm_model="gpt-4o-mini", llm_input_tokens=tokens_in,
            llm_output_tokens=300,
        ))
        # a route without LLM calls never gets a cost alert
        rows.append(queries.event_row(
            event_id=str(uuid.uuid4()), time=hour + timedelta(minutes=i), service_name=service,
            method="GET", route="/users", status_code=200, duration_ms=20.0, is_error=False,
            error_type=None, host="h",
        ))
    return rows


def test_prompt_bloat_opens_and_resolves_an_llm_cost_alert():
    service = f"test-llmcost-{uuid.uuid4().hex[:8]}"

    async def run():
        pool = await pool_module.create_pool()
        try:
            rows = []
            for k in range(1, 9):
                rows += _hour_rows(service, HOUR - timedelta(weeks=k), tokens_in=1500)
            rows += _hour_rows(service, HOUR, tokens_in=9000)  # the prompt grew 6x
            await queries.insert_events(pool, rows)
            async with pool.acquire() as conn:
                await conn.execute("CALL refresh_continuous_aggregate('llm_usage_1hour', NULL, NULL)")

            # 20 calls an hour is cheap, so use a low floor: +$0.0225 this hour
            opened = await llm_cost.check_service(
                pool, service, HOUR, HOUR + timedelta(minutes=75), CHANNELS, RENOTIFY, min_usd=0.01)
            quiet_floor = await llm_cost.check_service(
                pool, service, HOUR, HOUR + timedelta(minutes=80), CHANNELS, RENOTIFY, min_usd=1.0)

            # anomaly alerts for the same service don't touch the LLM cost alert
            await hourly.apply_detections(
                pool, service, HOUR + timedelta(hours=3), [], HOUR + timedelta(hours=3, minutes=15), RENOTIFY)
            still_open = await pool.fetch(
                "SELECT kind, route FROM alerts WHERE service_name = $1 AND resolved_at IS NULL", service)

            # two clean hours later (no LLM data at all in that hour) it resolves
            later = HOUR + timedelta(hours=2)
            resolved = await llm_cost.check_service(
                pool, service, later, later + timedelta(minutes=15), CHANNELS, RENOTIFY, min_usd=0.01)
            return opened, quiet_floor, [tuple(r) for r in still_open], resolved
        finally:
            await pool.execute("DELETE FROM alerts WHERE service_name = $1", service)
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool_module.close_pool()

    opened, quiet_floor, still_open, resolved = asyncio.run(run())

    [(event, alert)] = opened
    assert event == notifier.OPENED
    assert alert["kind"] == "llm_cost"
    assert alert["route"] == "/chat"
    details = alert["details"]
    assert details["cause"] == "unit_cost"
    assert details["baseline_samples"] == 8
    assert details["observed_cost_usd"] == pytest.approx(20 * (9000 * 0.15 + 300 * 0.60) / 1e6, abs=1e-4)
    assert details["drivers"][0]["factor"] == "tokens_per_call"
    assert "LLM cost spike" in notifier.format_text(event, service, "/chat", details)

    # on a $1 floor the same hour isn't a finding; the open alert just isn't renotified
    assert quiet_floor == []
    assert still_open == [("llm_cost", "/chat")]
    assert [(e, a["kind"]) for e, a in resolved] == [(notifier.RESOLVED, "llm_cost")]
