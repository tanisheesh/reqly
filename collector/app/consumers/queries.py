"""Consumer analytics: who calls the API, how much, and who a problem hit.

Consumer ids arrive already hashed by the SDK (HMAC with the app's salt),
so the collector never sees an API key. Windows up to 7 days read the raw
events (exact p95, current to the second); 30 days read the hourly rollup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
RAW_WINDOWS = ("24h", "7d")
TOP_CONSUMERS = 50
TOP_AFFECTED = 5


def window_start(window: str, now: datetime) -> datetime:
    try:
        return now - WINDOWS[window]
    except KeyError:
        raise ValueError(f"unsupported window: {window!r}")


def _rate(errors, requests):
    return round(errors / requests, 6) if requests else None


async def top_consumers(pool: asyncpg.Pool, service: str, window: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = window_start(window, now)
    if window in RAW_WINDOWS:
        totals = await pool.fetchrow(
            """
            SELECT count(*) AS requests, count(consumer_id) AS with_consumer,
                   count(DISTINCT consumer_id) AS consumers
            FROM request_events WHERE service_name = $1 AND time > $2
            """,
            service, start,
        )
        rows = await pool.fetch(
            f"""
            SELECT consumer_id, count(*) AS requests, count(*) FILTER (WHERE is_error) AS errors,
                   count(DISTINCT route) AS routes, max(time) AS last_seen,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
            FROM request_events
            WHERE service_name = $1 AND time > $2 AND consumer_id IS NOT NULL
            GROUP BY consumer_id ORDER BY requests DESC LIMIT {TOP_CONSUMERS}
            """,
            service, start,
        )
    else:
        totals = await pool.fetchrow(
            """
            SELECT coalesce(sum(request_count), 0) AS requests,
                   coalesce(sum(request_count) FILTER (WHERE consumer_id IS NOT NULL), 0) AS with_consumer,
                   count(DISTINCT consumer_id) AS consumers
            FROM consumer_usage_1hour WHERE service_name = $1 AND bucket > $2
            """,
            service, start,
        )
        rows = await pool.fetch(
            f"""
            SELECT consumer_id, sum(request_count) AS requests, sum(error_count) AS errors,
                   count(DISTINCT route) AS routes, max(bucket) + interval '1 hour' AS last_seen,
                   NULL::float AS p95_ms
            FROM consumer_usage_1hour
            WHERE service_name = $1 AND bucket > $2 AND consumer_id IS NOT NULL
            GROUP BY consumer_id ORDER BY requests DESC LIMIT {TOP_CONSUMERS}
            """,
            service, start,
        )
    requests = int(totals["requests"])
    return {
        "service_name": service,
        "window": window,
        "requests": requests,
        "requests_with_consumer": int(totals["with_consumer"]),
        "consumers": int(totals["consumers"]),
        "top": [
            {
                "consumer_id": r["consumer_id"],
                "requests": int(r["requests"]),
                "share_of_requests": round(int(r["requests"]) / requests, 6) if requests else None,
                "errors": int(r["errors"]),
                "error_rate": _rate(int(r["errors"]), int(r["requests"])),
                "routes": int(r["routes"]),
                "p95_ms": r["p95_ms"],
                "last_seen": r["last_seen"],
            }
            for r in rows
        ],
    }


async def consumer_detail(
    pool: asyncpg.Pool, service: str, consumer_id: str, window: str, now: datetime | None = None
) -> dict | None:
    now = now or datetime.now(timezone.utc)
    start = window_start(window, now)
    if window in RAW_WINDOWS:
        routes = await pool.fetch(
            """
            SELECT method, route, count(*) AS requests, count(*) FILTER (WHERE is_error) AS errors,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
                   max(time) AS last_seen
            FROM request_events
            WHERE service_name = $1 AND consumer_id = $2 AND time > $3
            GROUP BY method, route ORDER BY requests DESC
            """,
            service, consumer_id, start,
        )
        daily = await pool.fetch(
            """
            SELECT time_bucket('1 day', time) AS day, count(*) AS requests,
                   count(*) FILTER (WHERE is_error) AS errors
            FROM request_events
            WHERE service_name = $1 AND consumer_id = $2 AND time > $3
            GROUP BY 1 ORDER BY 1
            """,
            service, consumer_id, start,
        )
    else:
        routes = await pool.fetch(
            """
            SELECT method, route, sum(request_count) AS requests, sum(error_count) AS errors,
                   NULL::float AS p95_ms, max(bucket) + interval '1 hour' AS last_seen
            FROM consumer_usage_1hour
            WHERE service_name = $1 AND consumer_id = $2 AND bucket > $3
            GROUP BY method, route ORDER BY requests DESC
            """,
            service, consumer_id, start,
        )
        daily = await pool.fetch(
            """
            SELECT time_bucket('1 day', bucket) AS day, sum(request_count) AS requests,
                   sum(error_count) AS errors
            FROM consumer_usage_1hour
            WHERE service_name = $1 AND consumer_id = $2 AND bucket > $3
            GROUP BY 1 ORDER BY 1
            """,
            service, consumer_id, start,
        )
    if not routes:
        return None
    return {
        "service_name": service,
        "consumer_id": consumer_id,
        "window": window,
        "routes": [
            {
                "method": r["method"], "route": r["route"], "requests": int(r["requests"]),
                "errors": int(r["errors"]), "error_rate": _rate(int(r["errors"]), int(r["requests"])),
                "p95_ms": r["p95_ms"], "last_seen": r["last_seen"],
            }
            for r in routes
        ],
        "daily": [
            {"day": d["day"], "requests": int(d["requests"]), "errors": int(d["errors"])} for d in daily
        ],
    }


async def affected_consumers(
    conn, service: str, route: str, start: datetime, end: datetime, method: str | None = None
) -> dict | None:
    """Consumers that got errors on a route in a time range (an alert's
    hour). None when no request in the range carried a consumer id."""
    row = await conn.fetchrow(
        """
        SELECT count(DISTINCT consumer_id) AS active,
               count(DISTINCT consumer_id) FILTER (WHERE is_error) AS affected
        FROM request_events
        WHERE service_name = $1 AND route = $2 AND time >= $3 AND time < $4
          AND consumer_id IS NOT NULL AND ($5::text IS NULL OR method = $5)
        """,
        service, route, start, end, method,
    )
    if not row or not row["active"]:
        return None
    top = await conn.fetch(
        f"""
        SELECT consumer_id, count(*) AS requests, count(*) FILTER (WHERE is_error) AS errors
        FROM request_events
        WHERE service_name = $1 AND route = $2 AND time >= $3 AND time < $4
          AND consumer_id IS NOT NULL AND ($5::text IS NULL OR method = $5)
        GROUP BY consumer_id HAVING count(*) FILTER (WHERE is_error) > 0
        ORDER BY errors DESC, requests DESC LIMIT {TOP_AFFECTED}
        """,
        service, route, start, end, method,
    )
    return {
        "active": int(row["active"]),
        "affected": int(row["affected"]),
        "top": [
            {"consumer_id": r["consumer_id"], "requests": int(r["requests"]), "errors": int(r["errors"])}
            for r in top
        ],
    }


async def add_affected_consumers(pool: asyncpg.Pool, service: str, anomalies: list[dict]) -> None:
    """Adds "affected_consumers" to each anomaly dict in place (needs "route"
    and "window_start"; the anomaly covers one hour)."""
    if not anomalies:
        return
    async with pool.acquire() as conn:
        for anomaly in anomalies:
            start = datetime.fromisoformat(anomaly["window_start"])
            affected = await affected_consumers(conn, service, anomaly["route"], start, start + timedelta(hours=1))
            if affected is not None:
                anomaly["affected_consumers"] = affected


async def top_consumers_for_operation(
    pool: asyncpg.Pool, service: str, method: str, routes: list[str], since: datetime, limit: int = 5
) -> list[dict]:
    """Who still calls an operation (e.g. a deprecated one), from the rollup."""
    rows = await pool.fetch(
        """
        SELECT consumer_id, sum(request_count) AS requests, max(bucket) + interval '1 hour' AS last_seen
        FROM consumer_usage_1hour
        WHERE service_name = $1 AND method = $2 AND route = ANY($3::text[]) AND bucket > $4
          AND consumer_id IS NOT NULL
        GROUP BY consumer_id ORDER BY requests DESC LIMIT $5
        """,
        service, method, routes, since, limit,
    )
    return [{"consumer_id": r["consumer_id"], "requests": int(r["requests"]), "last_seen": r["last_seen"]} for r in rows]
