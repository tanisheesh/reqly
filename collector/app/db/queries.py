from __future__ import annotations

from datetime import date, datetime

import asyncpg

# Column order for insert_events rows. Build rows with event_row() rather
# than positional tuples so a new column can't silently shift values.
EVENT_COLUMNS: tuple[str, ...] = (
    "event_id",
    "time",
    "service_name",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "is_error",
    "error_type",
    "host",
    "release",
    "environment",
    "consumer_id",
    "request_bytes",
    "response_bytes",
    "llm_model",
    "llm_input_tokens",
    "llm_output_tokens",
)
_TIME_INDEX = EVENT_COLUMNS.index("time")

_INSERT_EVENT_SQL = f"""
INSERT INTO request_events ({", ".join(EVENT_COLUMNS)})
VALUES ({", ".join(f"${i}" for i in range(1, len(EVENT_COLUMNS) + 1))})
ON CONFLICT (time, event_id) DO NOTHING
"""


def event_row(**values) -> tuple:
    """One insert_events row. Required: every v1 column; the v2 columns
    (release, environment, consumer_id, bytes, llm_*) default to NULL."""
    unknown = set(values) - set(EVENT_COLUMNS)
    if unknown:
        raise TypeError(f"unknown event columns: {sorted(unknown)}")
    return tuple(values.get(column) for column in EVENT_COLUMNS)


def row_time(row: tuple) -> datetime:
    return row[_TIME_INDEX]


async def insert_events(pool: asyncpg.Pool, rows: list[tuple]) -> None:
    """Bulk insert via executemany. `rows` come from event_row(). event_id
    is the dedup key (ON CONFLICT DO NOTHING) so a batch retried by the
    SDK's shipper after a timeout can't double-count events that actually
    succeeded server-side.
    """
    if not rows:
        return
    async with pool.acquire() as conn:
        await conn.executemany(_INSERT_EVENT_SQL, rows)


_UPSERT_DEPLOYMENT_SQL = """
INSERT INTO deployments (service_name, environment, release, first_seen_at, last_seen_at)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (service_name, environment, release) DO UPDATE
SET first_seen_at = LEAST(deployments.first_seen_at, EXCLUDED.first_seen_at),
    last_seen_at  = GREATEST(deployments.last_seen_at, EXCLUDED.last_seen_at)
"""


async def record_deployments(pool: asyncpg.Pool, rows: list[tuple]) -> None:
    """Upserts one deployments row per (service, environment, release) in
    the batch, widening its first/last-seen range. Rows without a release
    are ignored."""
    service_i = EVENT_COLUMNS.index("service_name")
    env_i = EVENT_COLUMNS.index("environment")
    release_i = EVENT_COLUMNS.index("release")

    seen: dict[tuple[str, str, str], list[datetime]] = {}
    for row in rows:
        if not row[release_i]:
            continue
        key = (row[service_i], row[env_i] or "", row[release_i])
        ts = row_time(row)
        span = seen.get(key)
        if span is None:
            seen[key] = [ts, ts]
        else:
            span[0] = min(span[0], ts)
            span[1] = max(span[1], ts)
    if not seen:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            _UPSERT_DEPLOYMENT_SQL,
            [(svc, env, rel, first, last) for (svc, env, rel), (first, last) in seen.items()],
        )


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


# Chart resolution per window when reading the sketch aggregate: sketches
# merge exactly across time, so long windows are re-bucketed instead of
# shipping 10k one-minute points for 7 days.
_WINDOW_TO_BUCKET = {
    "1h": "1 minute",
    "6h": "5 minutes",
    "24h": "15 minutes",
    "7d": "1 hour",
}

_sketches_available: bool | None = None


async def sketches_available(pool: asyncpg.Pool) -> bool:
    """True when migration 003 created api_latency_1min (TimescaleDB Toolkit
    installed). Cached once known to be True; re-checked while False so a
    collector started before the migration picks it up without a restart."""
    global _sketches_available
    if not _sketches_available:
        _sketches_available = bool(
            await pool.fetchval("SELECT to_regclass('api_latency_1min') IS NOT NULL")
        )
    return _sketches_available


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
    if await sketches_available(pool):
        # Real percentiles at any level: sketches roll up across routes (and
        # methods/environments) and across time into one distribution.
        rows = await pool.fetch(
            f"""
            SELECT time_bucket('{_WINDOW_TO_BUCKET[window]}', bucket) AS bucket,
                   sum(request_count) AS request_count,
                   approx_percentile(0.50, rollup(latency)) AS p50_ms,
                   approx_percentile(0.95, rollup(latency)) AS p95_ms,
                   approx_percentile(0.99, rollup(latency)) AS p99_ms
            FROM api_latency_1min
            WHERE service_name = $1 AND ($2::text IS NULL OR route = $2)
              AND bucket > now() - interval '{interval}'
            GROUP BY 1 ORDER BY 1
            """,
            service_name,
            route,
        )
        return [dict(r) for r in rows]

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
    if await sketches_available(pool):
        # The 1-minute aggregate is current to the last minute, so every
        # window gets fresh error rates (the hourly view lags by up to 2h).
        interval = _interval_for_window(window)
        rows = await pool.fetch(
            f"""
            SELECT time_bucket('{_WINDOW_TO_BUCKET[window]}', bucket) AS bucket,
                   sum(request_count) AS request_count,
                   sum(error_count) AS error_count,
                   sum(error_count)::float / nullif(sum(request_count), 0) AS error_rate
            FROM api_latency_1min
            WHERE service_name = $1 AND ($2::text IS NULL OR route = $2)
              AND bucket > now() - interval '{interval}'
            GROUP BY 1 ORDER BY 1
            """,
            service_name,
            route,
        )
        return [dict(r) for r in rows]

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
    if await sketches_available(pool):
        rows = await pool.fetch(
            f"""
            SELECT route,
                   sum(request_count) AS request_count,
                   approx_percentile(0.95, rollup(latency)) AS p95_ms,
                   coalesce(sum(error_count)::float / nullif(sum(request_count), 0), 0) AS error_rate
            FROM api_latency_1min
            WHERE service_name = $1 AND bucket > now() - interval '{interval}'
            GROUP BY route
            ORDER BY request_count DESC
            LIMIT 20
            """,
            service_name,
        )
        return [dict(r) for r in rows]

    # For longer windows, use the pre-aggregated views for performance.
    # max(p95_ms) over time buckets per route: conservative upper bound, avoids
    # the invalid avg-of-percentiles pattern while staying in the right direction.
    # Each view is reduced to one row per route BEFORE joining -- joining the
    # 1-minute rows to hourly rows directly repeats every hourly error row once
    # per active minute, which skews the error rate toward busy hours.
    rows = await pool.fetch(
        f"""
        WITH latency AS (
            SELECT route,
                   sum(request_count) AS request_count,
                   max(p95_ms) AS p95_ms
            FROM route_latency_1min
            WHERE service_name = $1 AND bucket > now() - interval '{interval}'
            GROUP BY route
        ),
        errors AS (
            SELECT route,
                   sum(error_count)::float / nullif(sum(request_count), 0) AS error_rate
            FROM route_errors_1hour
            WHERE service_name = $1 AND bucket > now() - interval '{interval}'
            GROUP BY route
        )
        SELECT l.route, l.request_count, l.p95_ms, coalesce(e.error_rate, 0) AS error_rate
        FROM latency l
        LEFT JOIN errors e USING (route)
        ORDER BY l.request_count DESC
        LIMIT 20
        """,
        service_name,
    )
    return [dict(r) for r in rows]


RELEASE_STATS_DAYS = 14  # raw-event retention: per-release stats can't look further back


async def list_releases(pool: asyncpg.Pool, service_name: str, limit: int = 20) -> list[dict]:
    """Most recent releases of a service, newest first, with request volume,
    error rate and p95 over the raw-retention window. Releases older than
    that window still appear (from the deployments table) with zero stats."""
    deployments = await pool.fetch(
        """
        SELECT release,
               min(first_seen_at) AS first_seen_at,
               max(last_seen_at) AS last_seen_at,
               array_remove(array_agg(DISTINCT nullif(environment, '')), NULL) AS environments
        FROM deployments
        WHERE service_name = $1
        GROUP BY release
        ORDER BY first_seen_at DESC
        LIMIT $2
        """,
        service_name,
        limit,
    )
    if not deployments:
        return []
    stats = await pool.fetch(
        f"""
        SELECT release,
               count(*) AS request_count,
               avg(is_error::int)::float AS error_rate,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
        FROM request_events
        WHERE service_name = $1 AND release = ANY($2::text[])
          AND time > now() - interval '{RELEASE_STATS_DAYS} days'
        GROUP BY release
        """,
        service_name,
        [d["release"] for d in deployments],
    )
    by_release = {s["release"]: s for s in stats}
    releases = []
    for d in deployments:
        s = by_release.get(d["release"])
        releases.append({
            "release": d["release"],
            "first_seen_at": d["first_seen_at"],
            "last_seen_at": d["last_seen_at"],
            "environments": list(d["environments"] or []),
            "request_count": s["request_count"] if s else 0,
            "error_rate": s["error_rate"] if s else None,
            "p95_ms": s["p95_ms"] if s else None,
        })
    return releases


async def get_release_markers(pool: asyncpg.Pool, service_name: str, window: str) -> list[dict]:
    """Releases first seen inside the window -- drawn as deploy markers on
    the dashboard's time-series charts."""
    interval = _interval_for_window(window)
    rows = await pool.fetch(
        f"""
        SELECT release, min(first_seen_at) AS first_seen_at
        FROM deployments
        WHERE service_name = $1
        GROUP BY release
        HAVING min(first_seen_at) > now() - interval '{interval}'
        ORDER BY first_seen_at
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
