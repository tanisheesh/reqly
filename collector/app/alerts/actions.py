"""On-call actions -- acknowledge, silence, mute -- and the one place that
applies them before a notification goes out (see migrations/012)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from . import notifier

logger = logging.getLogger("reqly.collector")

MAX_SILENCE = timedelta(days=7)
MAX_MUTE = timedelta(days=30)


async def should_notify(pool: asyncpg.Pool, event: str, alert: dict, now: datetime) -> bool:
    row = await pool.fetchrow(
        """
        SELECT a.acknowledged_at, a.silenced_until,
               EXISTS (
                   SELECT 1 FROM alert_mutes m
                   WHERE m.service_name = a.service_name AND m.muted_until > $2
                     AND (m.route IS NULL OR m.route = a.route) AND (m.kind IS NULL OR m.kind = a.kind)
               ) AS muted
        FROM alerts a WHERE a.id = $1
        """,
        alert["id"], now,
    )
    if row is None:
        return True
    if row["muted"] or (row["silenced_until"] is not None and row["silenced_until"] > now):
        return False
    # Acknowledged: someone is on it, so no reminders -- but say when it's over.
    return not (row["acknowledged_at"] is not None and event == notifier.STILL_FIRING)


async def deliver(
    pool: asyncpg.Pool, channels: notifier.Channels, event: str, alert: dict, now: datetime | None = None
) -> int:
    """notifier.send(), unless the alert is acknowledged, silenced or muted."""
    if not channels.any:
        return 0
    if not await should_notify(pool, event, alert, now or datetime.now(timezone.utc)):
        logger.info("alert %s %s: not sent (acknowledged, silenced or muted)", alert["id"], event)
        return 0
    return await notifier.send(channels, event, alert)


async def acknowledge(pool: asyncpg.Pool, alert_id: int, actor: str, now: datetime) -> asyncpg.Record | None:
    """Keeps the first acknowledgement; None if the alert isn't open."""
    return await pool.fetchrow(
        """
        UPDATE alerts SET acknowledged_at = coalesce(acknowledged_at, $2),
                          acknowledged_by = coalesce(acknowledged_by, $3)
        WHERE id = $1 AND resolved_at IS NULL
        RETURNING id, acknowledged_at, acknowledged_by
        """,
        alert_id, now, actor,
    )


async def silence(pool: asyncpg.Pool, alert_id: int, until: datetime | None) -> asyncpg.Record | None:
    """until None lifts the silence. None if the alert isn't open."""
    return await pool.fetchrow(
        "UPDATE alerts SET silenced_until = $2 WHERE id = $1 AND resolved_at IS NULL RETURNING id, silenced_until",
        alert_id, until,
    )


async def active_mutes(pool: asyncpg.Pool, services: list[str] | None, now: datetime) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, service_name, route, kind, muted_until, reason, created_by, created_at
        FROM alert_mutes
        WHERE muted_until > $1 AND ($2::text[] IS NULL OR service_name = ANY($2::text[]))
        ORDER BY muted_until DESC
        """,
        now, services,
    )
    return [dict(r) for r in rows]


async def add_mute(
    pool: asyncpg.Pool, service: str, route: str | None, kind: str | None, until: datetime,
    reason: str | None, actor: str,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO alert_mutes (service_name, route, kind, muted_until, reason, created_by)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, service_name, route, kind, muted_until, reason, created_by, created_at
        """,
        service, route, kind, until, reason, actor,
    )
    return dict(row)
