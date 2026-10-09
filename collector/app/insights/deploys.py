"""Deploy-aware context for detected anomalies.

For each anomaly this answers: which release was serving that route during
the anomalous hour, was that release new (first seen within the week before
the anomaly, i.e. after at least part of the baseline), and how did the
route behave on it compared with the release before it?

Deterministic and LLM-free; the LLM only gets the resulting numbers. Pure
module (asyncpg only, no app imports) so the SAM Lambda vendors it as-is --
see infra/sam/sync_insights.py.

Reads raw request_events, so it covers the raw retention window (14 days),
which comfortably includes the 7-day "recent" window anomalies come from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg

# A release counts as "new" for an anomaly if it was first seen less than
# this long before the anomalous hour, i.e. the baseline weeks ran on
# something else.
NEW_RELEASE_WINDOW = timedelta(days=7)

# How much traffic on each side of a deploy to compare.
COMPARISON_WINDOW = timedelta(days=7)

_DOMINANT_RELEASE_SQL = """
SELECT release, count(*) AS n
FROM request_events
WHERE service_name = $1 AND route = $2 AND time >= $3 AND time < $4
  AND release IS NOT NULL
GROUP BY release
ORDER BY n DESC
LIMIT 1
"""

_FIRST_SEEN_SQL = """
SELECT min(first_seen_at) FROM deployments WHERE service_name = $1 AND release = $2
"""

_PREVIOUS_RELEASE_SQL = """
SELECT release, min(first_seen_at) AS first_seen_at
FROM deployments
WHERE service_name = $1 AND release <> $2
GROUP BY release
HAVING min(first_seen_at) < $3
ORDER BY first_seen_at DESC
LIMIT 1
"""

_ROUTE_RELEASE_STATS_SQL = """
SELECT count(*) AS requests,
       avg(is_error::int)::float AS error_rate,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
FROM request_events
WHERE service_name = $1 AND route = $2 AND release = $3 AND time >= $4 AND time < $5
"""


def _stats(row) -> dict:
    if row is None or not row["requests"]:
        return {"requests": 0, "error_rate": None, "p95_ms": None}
    return {
        "requests": row["requests"],
        "error_rate": round(row["error_rate"], 4),
        "p95_ms": round(row["p95_ms"], 1),
    }


async def release_context(
    conn: asyncpg.Connection,
    service_name: str,
    route: str,
    window_start: datetime,
    now: datetime | None = None,
) -> dict | None:
    """Release context for one anomalous hour, or None when the events in
    that hour carry no release (SDK/exporter doesn't send one)."""
    now = now or datetime.now(timezone.utc)
    window_end = window_start + timedelta(hours=1)

    dominant = await conn.fetchrow(
        _DOMINANT_RELEASE_SQL, service_name, route, window_start, window_end
    )
    if dominant is None:
        return None
    release = dominant["release"]
    first_seen = await conn.fetchval(_FIRST_SEEN_SQL, service_name, release)
    if first_seen is None:  # events predate the deployments table
        return {"release": release, "release_first_seen_at": None, "is_new_release": False}

    context = {
        "release": release,
        "release_first_seen_at": first_seen.isoformat(),
        "is_new_release": first_seen > window_start - NEW_RELEASE_WINDOW,
    }
    if not context["is_new_release"]:
        return context

    previous = await conn.fetchrow(_PREVIOUS_RELEASE_SQL, service_name, release, first_seen)
    after = await conn.fetchrow(
        _ROUTE_RELEASE_STATS_SQL, service_name, route, release,
        first_seen, min(now, first_seen + COMPARISON_WINDOW),
    )
    context["after"] = _stats(after)
    if previous is not None:
        before = await conn.fetchrow(
            _ROUTE_RELEASE_STATS_SQL, service_name, route, previous["release"],
            max(previous["first_seen_at"], first_seen - COMPARISON_WINDOW), first_seen,
        )
        context["previous_release"] = previous["release"]
        context["before"] = _stats(before)
    return context


async def add_release_context(
    pool: asyncpg.Pool,
    service_name: str,
    anomalies: list[dict],
    now: datetime | None = None,
) -> None:
    """Adds a "release_context" key (dict or None) to each anomaly dict, in
    place. Anomalies need "route" and "window_start" (ISO 8601)."""
    if not anomalies:
        return
    async with pool.acquire() as conn:
        for anomaly in anomalies:
            anomaly["release_context"] = await release_context(
                conn,
                service_name,
                anomaly["route"],
                datetime.fromisoformat(anomaly["window_start"]),
                now=now,
            )
