"""Hourly anomaly check -> alerts.

Every hour (at :15) the last completed hour of each service is compared with
the same weekday-hour in the previous 8 weeks, using the same statistics as
the weekly report. Each anomalous route opens an alert (or updates its open
one); an open alert resolves after RESOLVE_AFTER without a detection.
Notifications go out on open, every RENOTIFY_AFTER while it keeps firing,
and on resolve.

Run from a single collector instance: alert state lives in the database, but
two collectors checking the same hour at the same moment could both open an
alert before either commits (the unique index then rejects one).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from ..insights.anomaly_detection import detect_anomalies
from ..insights.deploys import add_release_context
from ..insights.hints import add_hints
from . import notifier

logger = logging.getLogger("reqly.collector")

BASELINE_WEEKS = 8
RESOLVE_AFTER = timedelta(hours=2)

_HOURLY_ROWS_SQL = """
SELECT bucket, route, request_count, error_count, error_rate, p95_ms
FROM route_errors_1hour
WHERE service_name = $1 AND bucket = ANY($2::timestamptz[])
"""


def last_complete_hour(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)


async def _hour_rows(pool: asyncpg.Pool, service_name: str, hour: datetime) -> list[dict]:
    slots = [hour - timedelta(weeks=k) for k in range(BASELINE_WEEKS + 1)]
    rows = await pool.fetch(_HOURLY_ROWS_SQL, service_name, slots)
    return [dict(r) for r in rows]


async def _refresh_hour(pool: asyncpg.Pool, hour: datetime) -> None:
    # route_errors_1hour's policy stops an hour short of now (end_offset), so
    # the hour being checked isn't materialized yet; refresh it explicitly.
    async with pool.acquire() as conn:
        await conn.execute(
            "CALL refresh_continuous_aggregate('route_errors_1hour', $1::timestamptz, $2::timestamptz)",
            hour,
            hour + timedelta(hours=1),
        )


def _alert_payload(row) -> dict:
    details = row["details"]
    if isinstance(details, str):
        details = json.loads(details)
    return {
        "id": row["id"],
        "service_name": row["service_name"],
        "route": row["route"],
        "opened_at": row["opened_at"].isoformat(),
        "first_hour": row["first_hour"].isoformat(),
        "last_hour": row["last_hour"].isoformat(),
        "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
        "details": details,
    }


async def apply_detections(
    pool: asyncpg.Pool,
    service_name: str,
    hour: datetime,
    anomalies: list[dict],
    now: datetime,
    renotify_after: timedelta,
) -> list[tuple[str, dict]]:
    """Opens/updates/resolves alerts for one service and hour. Returns the
    (event, alert) notifications to send. Pure DB state machine -- no I/O to
    notification channels -- so it can be tested on its own."""
    events: list[tuple[str, dict]] = []
    detected = {a["route"]: a for a in anomalies}
    async with pool.acquire() as conn:
        async with conn.transaction():
            open_alerts = {
                r["route"]: r
                for r in await conn.fetch(
                    "SELECT * FROM alerts WHERE service_name = $1 AND kind = 'anomaly' "
                    "AND resolved_at IS NULL FOR UPDATE",
                    service_name,
                )
            }
            for route, anomaly in detected.items():
                existing = open_alerts.get(route)
                if existing is None:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO alerts (service_name, route, first_hour, last_hour, last_notified_at, details)
                        VALUES ($1, $2, $3, $3, $4, $5::jsonb) RETURNING *
                        """,
                        service_name, route, hour, now, json.dumps(anomaly),
                    )
                    events.append((notifier.OPENED, _alert_payload(row)))
                    continue
                renotify = (
                    existing["last_notified_at"] is None
                    or now - existing["last_notified_at"] >= renotify_after
                )
                row = await conn.fetchrow(
                    """
                    UPDATE alerts
                    SET last_hour = GREATEST(last_hour, $2), details = $3::jsonb,
                        last_notified_at = CASE WHEN $4 THEN $5 ELSE last_notified_at END
                    WHERE id = $1 RETURNING *
                    """,
                    existing["id"], hour, json.dumps(anomaly), renotify, now,
                )
                if renotify:
                    events.append((notifier.STILL_FIRING, _alert_payload(row)))

            for route, existing in open_alerts.items():
                if route in detected:
                    continue
                if hour - existing["last_hour"] >= RESOLVE_AFTER:
                    row = await conn.fetchrow(
                        "UPDATE alerts SET resolved_at = $2 WHERE id = $1 RETURNING *",
                        existing["id"], now,
                    )
                    events.append((notifier.RESOLVED, _alert_payload(row)))
    return events


async def check_service(
    pool: asyncpg.Pool,
    service_name: str,
    hour: datetime,
    now: datetime,
    channels: notifier.Channels,
    renotify_after: timedelta,
) -> list[tuple[str, dict]]:
    rows = await _hour_rows(pool, service_name, hour)
    anomalies = [
        a.to_dict()
        for a in detect_anomalies(rows, now=hour + timedelta(hours=1), recent_window=timedelta(hours=1))
    ]
    for enrich in (add_release_context, add_hints):
        try:
            await enrich(pool, service_name, anomalies)
        except Exception:
            logger.exception("alert enrichment %s failed for service=%s", enrich.__name__, service_name)

    events = await apply_detections(pool, service_name, hour, anomalies, now, renotify_after)
    for event, alert in events:
        await notifier.send(channels, event, alert)
    return events


async def run_hourly_check(
    pool: asyncpg.Pool,
    services: list[str],
    channels: notifier.Channels,
    renotify_after: timedelta,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    hour = last_complete_hour(now)
    await _refresh_hour(pool, hour)
    total = 0
    for service_name in services:
        try:
            total += len(await check_service(pool, service_name, hour, now, channels, renotify_after))
        except Exception:
            logger.exception("hourly alert check failed for service=%s", service_name)
    logger.info("Reqly collector: hourly alert check for %s -> %d notifications", hour.isoformat(), total)
    return total
