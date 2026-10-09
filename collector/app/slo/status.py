"""SLO definitions and their live status, read from TimescaleDB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg

from ..db.queries import sketches_available
from .model import SHORT_WINDOWS, Counts, evaluate

RAW_RETENTION = timedelta(days=14)

_SLO_COLUMNS = (
    "id, service_name, name, route, objective, target, latency_threshold_ms, "
    "window_days, created_at, updated_at"
)


async def list_slos(pool: asyncpg.Pool, service_name: str | None = None) -> list[dict]:
    rows = await pool.fetch(
        f"SELECT {_SLO_COLUMNS} FROM slos WHERE ($1::text IS NULL OR service_name = $1) "
        "ORDER BY service_name, name",
        service_name,
    )
    return [dict(r) for r in rows]


async def upsert_slo(pool: asyncpg.Pool, slo: dict) -> dict:
    row = await pool.fetchrow(
        f"""
        INSERT INTO slos (service_name, name, route, objective, target, latency_threshold_ms, window_days)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (service_name, name) DO UPDATE
        SET route = EXCLUDED.route, objective = EXCLUDED.objective, target = EXCLUDED.target,
            latency_threshold_ms = EXCLUDED.latency_threshold_ms,
            window_days = EXCLUDED.window_days, updated_at = now()
        RETURNING {_SLO_COLUMNS}
        """,
        slo["service_name"], slo["name"], slo.get("route"), slo["objective"], slo["target"],
        slo.get("latency_threshold_ms"), slo.get("window_days", 28),
    )
    return dict(row)


async def delete_slo(pool: asyncpg.Pool, slo_id: int) -> bool:
    return (await pool.execute("DELETE FROM slos WHERE id = $1", slo_id)) == "DELETE 1"


def _bad_expression(slo: dict) -> tuple[str, list]:
    if slo["objective"] == "availability":
        return "is_error", []
    return "duration_ms > $5", [slo["latency_threshold_ms"]]


async def _short_window_counts(pool, slo: dict, now: datetime) -> dict[str, Counts]:
    """Exact counts for the alerting windows (<= 6h) from raw events, which
    are current to the second -- the aggregates lag by up to a minute."""
    bad_expr, extra = _bad_expression(slo)
    selects = ", ".join(
        f"count(*) FILTER (WHERE time > $3::timestamptz - interval '{int(w.total_seconds())} seconds') AS n_{name}, "
        f"count(*) FILTER (WHERE time > $3::timestamptz - interval '{int(w.total_seconds())} seconds' AND {bad_expr}) AS b_{name}"
        for name, w in SHORT_WINDOWS.items()
    )
    longest = max(SHORT_WINDOWS.values())
    row = await pool.fetchrow(
        f"""
        SELECT {selects}
        FROM request_events
        WHERE service_name = $1 AND ($2::text IS NULL OR route = $2)
          AND time > $3::timestamptz - $4::interval AND time <= $3::timestamptz
        """,
        slo["service_name"], slo["route"], now, longest, *extra,
    )
    return {name: Counts(bad=row[f"b_{name}"], total=row[f"n_{name}"]) for name in SHORT_WINDOWS}


async def _window_counts(pool, slo: dict, now: datetime) -> tuple[Counts, int]:
    """(counts over the SLO window, days of window actually covered)."""
    window = timedelta(days=slo["window_days"])
    start = now - window
    if await sketches_available(pool):
        if slo["objective"] == "availability":
            row = await pool.fetchrow(
                """
                SELECT coalesce(sum(request_count), 0) AS total, coalesce(sum(error_count), 0) AS bad
                FROM api_latency_1min
                WHERE service_name = $1 AND ($2::text IS NULL OR route = $2) AND bucket > $3
                """,
                slo["service_name"], slo["route"], start,
            )
            return Counts(bad=float(row["bad"]), total=int(row["total"])), slo["window_days"]
        # Share of requests at or under the threshold, read straight off the
        # merged latency sketch.
        row = await pool.fetchrow(
            """
            SELECT coalesce(sum(request_count), 0) AS total,
                   approx_percentile_rank($4, rollup(latency)) AS good_fraction
            FROM api_latency_1min
            WHERE service_name = $1 AND ($2::text IS NULL OR route = $2) AND bucket > $3
            """,
            slo["service_name"], slo["route"], start, slo["latency_threshold_ms"],
        )
        total = int(row["total"])
        good = row["good_fraction"] if row["good_fraction"] is not None else 1.0
        return Counts(bad=total * (1 - good), total=total), slo["window_days"]

    # Without the Toolkit: raw events, so at most the raw retention window.
    covered = min(window, RAW_RETENTION)
    bad_expr, extra = _bad_expression(slo)
    row = await pool.fetchrow(
        f"""
        SELECT count(*) AS total, count(*) FILTER (WHERE {bad_expr}) AS bad
        FROM request_events
        WHERE service_name = $1 AND ($2::text IS NULL OR route = $2) AND time > $3 AND time <= $4
        """,
        slo["service_name"], slo["route"], now - covered, now, *extra,
    )
    return Counts(bad=float(row["bad"]), total=int(row["total"])), covered.days


async def slo_status(pool: asyncpg.Pool, slo: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    window, covered_days = await _window_counts(pool, slo, now)
    short = await _short_window_counts(pool, slo, now)
    status = evaluate(slo["target"], window, short)
    status["window_days_covered"] = covered_days
    return status
