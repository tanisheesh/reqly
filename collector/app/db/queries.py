from __future__ import annotations

from datetime import date, datetime

import asyncpg

_INSERT_EVENT_SQL = """
INSERT INTO request_events
    (event_id, time, service_name, method, route, status_code, duration_ms, is_error, error_type, host)
VALUES
    ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (time, event_id) DO NOTHING
"""


async def insert_events(pool: asyncpg.Pool, rows: list[tuple]) -> None:
    """Bulk insert via executemany. `rows` are already-validated tuples in
    the exact column order of _INSERT_EVENT_SQL. event_id is the dedup key
    (ON CONFLICT DO NOTHING) so a batch retried by the SDK's shipper after a
    timeout can't double-count events that actually succeeded server-side.
    """
    if not rows:
        return
    async with pool.acquire() as conn:
        await conn.executemany(_INSERT_EVENT_SQL, rows)


_WINDOW_TO_INTERVAL = {
    "1h": "1 hour",
    "6h": "6 hours",
    "24h": "24 hours",
    "7d": "7 days",
}


def _interval_for_window(window: str) -> str:
    try:
        return _WINDOW_TO_INTERVAL[window]
    except KeyError:
        raise ValueError(f"unsupported window: {window!r}")


async def list_services(pool: asyncpg.Pool) -> list[str]:
    # Query the 90-day aggregate instead of the 14-day raw events table so
    # services that have been quiet for >2 weeks remain visible in the dropdown.
    rows = await pool.fetch(
        "SELECT DISTINCT service_name FROM route_latency_1min ORDER BY service_name"
    )
    return [r["service_name"] for r in rows]


async def list_routes(pool: asyncpg.Pool, service_name: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT DISTINCT route FROM route_latency_1min WHERE service_name = $1 ORDER BY route",
        service_name,
    )
    return [r["route"] for r in rows]


async def get_latency_series(
    pool: asyncpg.Pool, service_name: str, route: str | None, window: str
) -> list[dict]:
    interval = _interval_for_window(window)
    if route:
        # Single route: one row per (bucket, route) so avg == the value itself.
        rows = await pool.fetch(
            f"""
            SELECT bucket, sum(request_count) AS request_count,
                   avg(p50_ms) AS p50_ms, avg(p95_ms) AS p95_ms, avg(p99_ms) AS p99_ms
            FROM route_latency_1min
            WHERE service_name = $1 AND route = $2 AND bucket > now() - interval '{interval}'
            GROUP BY bucket ORDER BY bucket
            """,
            service_name,
            route,
        )
    else:
        # Service-level: multiple routes per bucket. You cannot average percentiles —
        # avg(p95) across routes is statistically meaningless. Use max() as a
        # conservative upper bound: the true service p95 is <= max(route p95s).
        rows = await pool.fetch(
            f"""
            SELECT bucket, sum(request_count) AS request_count,
                   max(p50_ms) AS p50_ms, max(p95_ms) AS p95_ms, max(p99_ms) AS p99_ms
            FROM route_latency_1min
            WHERE service_name = $1 AND bucket > now() - interval '{interval}'
            GROUP BY bucket ORDER BY bucket
            """,
            service_name,
        )
    return [dict(r) for r in rows]


async def get_error_rate_series(
    pool: asyncpg.Pool, service_name: str, route: str | None, window: str
) -> list[dict]:
    # route_errors_1hour has end_offset=1h so the last full hour is always a gap.
    # For the 1h window that gap covers the entire range — fall back to raw events
    # with 5-minute resolution so the chart isn't empty.
    if window == "1h":
        if route:
            rows = await pool.fetch(
                """
                SELECT
                    time_bucket('5 minutes', time) AS bucket,
                    count(*) AS request_count,
                    count(*) FILTER (WHERE is_error) AS error_count,
                    CASE WHEN count(*) > 0
                         THEN (count(*) FILTER (WHERE is_error))::float / count(*)
                         ELSE 0 END AS error_rate
                FROM request_events
                WHERE service_name = $1 AND route = $2 AND time > now() - INTERVAL '1 hour'
                GROUP BY bucket ORDER BY bucket
                """,
                service_name,
                route,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT
                    time_bucket('5 minutes', time) AS bucket,
                    count(*) AS request_count,
                    count(*) FILTER (WHERE is_error) AS error_count,
                    CASE WHEN count(*) > 0
                         THEN (count(*) FILTER (WHERE is_error))::float / count(*)
                         ELSE 0 END AS error_rate
                FROM request_events
                WHERE service_name = $1 AND time > now() - INTERVAL '1 hour'
                GROUP BY bucket ORDER BY bucket
                """,
                service_name,
            )
        return [dict(r) for r in rows]

    interval = _interval_for_window(window)
    if route:
        rows = await pool.fetch(
            f"""
            SELECT bucket, sum(request_count) AS request_count,
                   sum(error_count) AS error_count,
                   CASE WHEN sum(request_count) > 0
                        THEN sum(error_count)::float / sum(request_count)
                        ELSE 0 END AS error_rate
            FROM route_errors_1hour
            WHERE service_name = $1 AND route = $2 AND bucket > now() - interval '{interval}'
            GROUP BY bucket ORDER BY bucket
            """,
            service_name,
            route,
        )
    else:
        rows = await pool.fetch(
            f"""
            SELECT bucket, sum(request_count) AS request_count,
                   sum(error_count) AS error_count,
                   CASE WHEN sum(request_count) > 0
                        THEN sum(error_count)::float / sum(request_count)
                        ELSE 0 END AS error_rate
            FROM route_errors_1hour
            WHERE service_name = $1 AND bucket > now() - interval '{interval}'
            GROUP BY bucket ORDER BY bucket
            """,
            service_name,
        )
    return [dict(r) for r in rows]


async def get_status_distribution(
    pool: asyncpg.Pool, service_name: str, route: str | None, window: str
) -> list[dict]:
    # Same end_offset gap as error rate — use raw events for the 1h window.
    if window == "1h":
        if route:
            rows = await pool.fetch(
                """
                SELECT status_code, count(*) AS count
                FROM request_events
                WHERE service_name = $1 AND route = $2 AND time > now() - INTERVAL '1 hour'
                GROUP BY status_code ORDER BY status_code
                """,
                service_name,
                route,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT status_code, count(*) AS count
                FROM request_events
                WHERE service_name = $1 AND time > now() - INTERVAL '1 hour'
                GROUP BY status_code ORDER BY status_code
                """,
                service_name,
            )
        return [dict(r) for r in rows]

    interval = _interval_for_window(window)
    if route:
        rows = await pool.fetch(
            f"""
            SELECT status_code, sum(count) AS count
            FROM route_status_distribution_1hour
            WHERE service_name = $1 AND route = $2 AND bucket > now() - interval '{interval}'
            GROUP BY status_code ORDER BY status_code
            """,
            service_name,
            route,
        )
    else:
        rows = await pool.fetch(
            f"""
            SELECT status_code, sum(count) AS count
            FROM route_status_distribution_1hour
            WHERE service_name = $1 AND bucket > now() - interval '{interval}'
            GROUP BY status_code ORDER BY status_code
            """,
            service_name,
        )
    return [dict(r) for r in rows]


async def get_top_routes(pool: asyncpg.Pool, service_name: str, window: str) -> list[dict]:
    # For the 1h window, route_errors_1hour has no data (end_offset=1h gap) so the
    # LEFT JOIN would zero out all error rates. Query raw events directly instead —
    # this also gives an accurate per-route p95 from the full distribution.
    if window == "1h":
        rows = await pool.fetch(
            """
            SELECT
                route,
                count(*) AS request_count,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
                CASE WHEN count(*) > 0
                     THEN (count(*) FILTER (WHERE is_error))::float / count(*)
                     ELSE 0 END AS error_rate
            FROM request_events
            WHERE service_name = $1 AND time > now() - INTERVAL '1 hour'
            GROUP BY route
            ORDER BY request_count DESC
            LIMIT 20
            """,
            service_name,
        )
        return [dict(r) for r in rows]

    interval = _interval_for_window(window)
    # For longer windows, use the pre-aggregated views for performance.
    # max(p95_ms) over time buckets per route: conservative upper bound, avoids
    # the invalid avg-of-percentiles pattern while staying in the right direction.
    rows = await pool.fetch(
        f"""
        SELECT l.route,
               sum(l.request_count) AS request_count,
               max(l.p95_ms) AS p95_ms,
               coalesce(sum(e.error_count)::float / nullif(sum(e.request_count), 0), 0) AS error_rate
        FROM route_latency_1min l
        LEFT JOIN route_errors_1hour e
            ON e.service_name = l.service_name AND e.route = l.route
            AND e.bucket = time_bucket('1 hour', l.bucket)
        WHERE l.service_name = $1 AND l.bucket > now() - interval '{interval}'
        GROUP BY l.route
        ORDER BY request_count DESC
        LIMIT 20
        """,
        service_name,
    )
    return [dict(r) for r in rows]


async def get_request_rate(pool: asyncpg.Pool, service_name: str) -> dict:
    """Polls the raw events table directly (not the 1-minute aggregate) for
    the last 60 seconds, so this one tile feels more 'live' in a demo without
    needing a push transport for the whole dashboard.
    """
    row = await pool.fetchrow(
        """
        SELECT count(*) AS request_count
        FROM request_events
        WHERE service_name = $1 AND time > now() - interval '60 seconds'
        """,
        service_name,
    )
    return {"requests_per_minute": row["request_count"] if row else 0}


# --- AI Insights ------------------------------------------------------------

async def get_hourly_seasonal_data(pool: asyncpg.Pool, service_name: str) -> list[dict]:
    """Trailing 8 weeks of hourly error-rate + p95 latency, used by the
    anomaly detector to build a day-of-week x hour-of-day baseline.
    """
    rows = await pool.fetch(
        """
        SELECT bucket, route, request_count, error_count, error_rate, p95_ms
        FROM route_errors_1hour
        WHERE service_name = $1 AND bucket > now() - interval '8 weeks'
        ORDER BY bucket
        """,
        service_name,
    )
    return [dict(r) for r in rows]


async def save_insight_report(
    pool: asyncpg.Pool,
    service_name: str,
    week_start: date,
    anomalies_json: str,
    report_text: str,
) -> None:
    await pool.execute(
        """
        INSERT INTO insight_reports (service_name, week_start, anomalies_json, report_text)
        VALUES ($1, $2, $3::jsonb, $4)
        ON CONFLICT (service_name, week_start)
        DO UPDATE SET anomalies_json = EXCLUDED.anomalies_json,
                      report_text = EXCLUDED.report_text,
                      generated_at = now()
        """,
        service_name,
        week_start,
        anomalies_json,
        report_text,
    )


async def get_latest_insight_report(pool: asyncpg.Pool, service_name: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT service_name, week_start, anomalies_json, report_text, generated_at
        FROM insight_reports
        WHERE service_name = $1
        ORDER BY week_start DESC
        LIMIT 1
        """,
        service_name,
    )
    return dict(row) if row else None
