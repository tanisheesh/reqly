"""Hourly LLM cost alerts.

Runs right after the error/latency check for the same hour: the route's LLM
cost in the last complete hour against the same weekday-hour in the previous
8 weeks (see app/llm/anomalies.py). Alerts share the alerts table and its
lifecycle with anomaly alerts (kind = 'llm_cost': one open per route, a
reminder every renotify interval, resolved after 2 clean hours).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import asyncpg

from ..llm.anomalies import detect_cost_anomalies
from ..llm.prices import price_table
from . import notifier
from .hourly import BASELINE_WEEKS, apply_detections

KIND = "llm_cost"

_ROWS_SQL = """
SELECT bucket, route, llm_model, request_count, input_tokens, output_tokens
FROM llm_usage_1hour
WHERE service_name = $1 AND bucket = ANY($2::timestamptz[])
"""


async def refresh_hour(pool: asyncpg.Pool, hour: datetime) -> None:
    # Like route_errors_1hour, the policy stops an hour short of now.
    async with pool.acquire() as conn:
        await conn.execute(
            "CALL refresh_continuous_aggregate('llm_usage_1hour', $1::timestamptz, $2::timestamptz)",
            hour,
            hour + timedelta(hours=1),
        )


async def hour_rows(pool: asyncpg.Pool, service_name: str, hour: datetime) -> list[dict]:
    slots = [hour - timedelta(weeks=k) for k in range(BASELINE_WEEKS + 1)]
    return [dict(r) for r in await pool.fetch(_ROWS_SQL, service_name, slots)]


async def check_service(
    pool: asyncpg.Pool,
    service_name: str,
    hour: datetime,
    now: datetime,
    channels: notifier.Channels,
    renotify_after: timedelta,
    min_usd: float,
) -> list[tuple[str, dict]]:
    rows = await hour_rows(pool, service_name, hour)
    findings = detect_cost_anomalies(rows, hour, price_table(), min_usd)
    events = await apply_detections(pool, service_name, hour, findings, now, renotify_after, kind=KIND)
    for event, alert in events:
        await notifier.send(channels, event, alert)
    return events
