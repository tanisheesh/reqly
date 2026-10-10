"""Stored specs and the observed traffic they are compared with."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import asyncpg

from ..db import queries
from .drift import Traffic, compare, spec_operations

DRIFT_WINDOW_DAYS = 30
RAW_RETENTION_DAYS = 14


async def save_spec(pool: asyncpg.Pool, service_name: str, spec: dict, base_path: str) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO api_specs (service_name, spec, base_path) VALUES ($1, $2::jsonb, $3)
        ON CONFLICT (service_name) DO UPDATE
        SET spec = EXCLUDED.spec, base_path = EXCLUDED.base_path, uploaded_at = now()
        RETURNING uploaded_at
        """,
        service_name, json.dumps(spec), base_path,
    )
    return {"uploaded_at": row["uploaded_at"]}


async def get_spec(pool: asyncpg.Pool, service_name: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT spec, base_path, uploaded_at FROM api_specs WHERE service_name = $1", service_name
    )
    if row is None:
        return None
    spec = row["spec"]
    return {
        "spec": json.loads(spec) if isinstance(spec, str) else spec,
        "base_path": row["base_path"],
        "uploaded_at": row["uploaded_at"],
    }


async def delete_spec(pool: asyncpg.Pool, service_name: str) -> bool:
    return (await pool.execute("DELETE FROM api_specs WHERE service_name = $1", service_name)) == "DELETE 1"


async def observed_traffic(pool: asyncpg.Pool, service_name: str, now: datetime) -> tuple[list[Traffic], int]:
    """(per method+route traffic, days covered). The sketch aggregate keeps
    the method for 90 days; without it, raw events cover 14."""
    if await queries.sketches_available(pool):
        days = DRIFT_WINDOW_DAYS
        rows = await pool.fetch(
            """
            SELECT method, route, sum(request_count) AS requests, sum(error_count) AS errors,
                   max(bucket) + interval '1 minute' AS last_seen
            FROM api_latency_1min
            WHERE service_name = $1 AND bucket > $2
            GROUP BY method, route
            """,
            service_name, now - timedelta(days=days),
        )
    else:
        days = min(DRIFT_WINDOW_DAYS, RAW_RETENTION_DAYS)
        rows = await pool.fetch(
            """
            SELECT method, route, count(*) AS requests, count(*) FILTER (WHERE is_error) AS errors,
                   max(time) AS last_seen
            FROM request_events
            WHERE service_name = $1 AND time > $2
            GROUP BY method, route
            """,
            service_name, now - timedelta(days=days),
        )
    traffic = [
        Traffic(method=r["method"].upper(), route=r["route"], requests=int(r["requests"]),
                errors=int(r["errors"]), last_seen=r["last_seen"])
        for r in rows
    ]
    return traffic, days


async def drift_report(pool: asyncpg.Pool, service_name: str, now: datetime | None = None) -> dict | None:
    """None when the service has no spec."""
    stored = await get_spec(pool, service_name)
    if stored is None:
        return None
    now = now or datetime.now(timezone.utc)
    spec = stored["spec"]
    operations = spec_operations(spec, stored["base_path"])
    traffic, days = await observed_traffic(pool, service_name, now)
    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    return {
        "service_name": service_name,
        "spec": {
            "title": info.get("title"),
            "version": info.get("version"),
            "base_path": stored["base_path"],
            "uploaded_at": stored["uploaded_at"],
        },
        "window_days": days,
        **compare(operations, traffic).to_dict(),
    }
