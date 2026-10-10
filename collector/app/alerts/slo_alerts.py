"""Burn-rate alerts for SLOs, checked every few minutes.

Shares the alerts table and notification channels with the hourly anomaly
alerts: kind = 'slo', one open alert per SLO (route column = 'slo:<name>').
An alert opens when an SLO enters fast or slow burn, reminds every
renotify interval while it keeps burning, and resolves once it has been
out of burn for RESOLVE_AFTER.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from ..slo import status as slo_status
from ..slo.model import BURNING_STATES
from . import notifier

logger = logging.getLogger("reqly.collector")

KIND = "slo"
RESOLVE_AFTER = timedelta(minutes=30)


def _alert_key(slo: dict) -> str:
    return f"slo:{slo['name']}"


def _jsonable(slo: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in slo.items()}


def _payload(row) -> dict:
    details = row["details"]
    if isinstance(details, str):
        details = json.loads(details)
    return {
        "id": row["id"],
        "kind": KIND,
        "service_name": row["service_name"],
        "route": row["route"],
        "opened_at": row["opened_at"].isoformat(),
        "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
        "details": details,
    }


async def apply_slo_status(
    pool: asyncpg.Pool, slo: dict, status: dict, now: datetime, renotify_after: timedelta
) -> tuple[str, dict] | None:
    """Opens/updates/resolves the alert for one SLO. Returns the
    notification to send, if any."""
    key = _alert_key(slo)
    burning = status["state"] in BURNING_STATES
    details = {"kind": KIND, "slo": _jsonable(slo), "status": status}
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT * FROM alerts WHERE service_name = $1 AND route = $2 AND kind = $3 "
                "AND resolved_at IS NULL FOR UPDATE",
                slo["service_name"], key, KIND,
            )
            if existing is None:
                if not burning:
                    return None
                row = await conn.fetchrow(
                    """
                    INSERT INTO alerts (service_name, route, kind, first_hour, last_hour, last_notified_at, details)
                    VALUES ($1, $2, $3, $4, $4, $4, $5::jsonb) RETURNING *
                    """,
                    slo["service_name"], key, KIND, now, json.dumps(details),
                )
                return notifier.OPENED, _payload(row)

            if burning:
                renotify = now - existing["last_notified_at"] >= renotify_after
                row = await conn.fetchrow(
                    """
                    UPDATE alerts SET last_hour = $2, details = $3::jsonb,
                        last_notified_at = CASE WHEN $4 THEN $2 ELSE last_notified_at END
                    WHERE id = $1 RETURNING *
                    """,
                    existing["id"], now, json.dumps(details), renotify,
                )
                return (notifier.STILL_FIRING, _payload(row)) if renotify else None

            if now - existing["last_hour"] >= RESOLVE_AFTER:
                row = await conn.fetchrow(
                    "UPDATE alerts SET resolved_at = $2, details = $3::jsonb WHERE id = $1 RETURNING *",
                    existing["id"], now, json.dumps(details),
                )
                return notifier.RESOLVED, _payload(row)
    return None


async def run_slo_check(
    pool: asyncpg.Pool,
    channels: notifier.Channels,
    renotify_after: timedelta,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    sent = 0
    for slo in await slo_status.list_slos(pool):
        try:
            status = await slo_status.slo_status(pool, slo, now)
            event = await apply_slo_status(pool, slo, status, now, renotify_after)
            if event is not None:
                await notifier.send(channels, *event)
                sent += 1
        except Exception:
            logger.exception("SLO check failed for %s/%s", slo["service_name"], slo["name"])
    return sent
