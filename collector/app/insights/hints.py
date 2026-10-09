"""Deterministic root-cause hints for an anomalous route and hour.

Answers "is this concentrated somewhere?" from raw events, without an LLM:

- host / environment: one value's share of the errors (or of the slow
  requests) is far above its share of the traffic -> "92% of errors came
  from host pod-7, which served 34% of requests". Comparing against the
  traffic share matters: a host serving 90% of requests producing 90% of
  errors is not a lead.
- error_type / status_code: one value dominates the errors and was rare or
  absent in the previous week -> "88% of errors are TimeoutError (not seen in
  the previous 7 days)".

Pure module (asyncpg only, no app imports) so the SAM Lambda vendors it
as-is -- see infra/sam/sync_insights.py. Reads raw request_events (14-day
retention), which covers the anomaly hour plus the 7-day comparison.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import asyncpg

BASELINE_WINDOW = timedelta(days=7)

MIN_ERRORS = 5  # fewer errors than this in the hour: too thin to attribute
MIN_SLOW = 10  # same for slow requests
MIN_SHARE = 0.6  # a value must account for this much of the errors/slow requests
MIN_TRAFFIC_LIFT = 2.0  # ...and at least this multiple of its traffic share
MIN_BASELINE_LIFT = 3.0  # ...or of its share of errors in the previous week

TRAFFIC_DIMENSIONS = ("host", "environment")
ERROR_DIMENSIONS = ("error_type", "status_code")


@dataclass
class Hint:
    dimension: str
    value: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Breakdown:
    """Per-value counts for one dimension within one time range."""

    requests: dict[str, int]
    errors: dict[str, int]
    slow: dict[str, int]

    @property
    def total_requests(self) -> int:
        return sum(self.requests.values())

    @property
    def total_errors(self) -> int:
        return sum(self.errors.values())

    @property
    def total_slow(self) -> int:
        return sum(self.slow.values())


def _pct(x: float) -> str:
    return f"{x:.0%}"


def traffic_hints(dimension: str, window: Breakdown) -> list[Hint]:
    """Errors or slow requests concentrated on one host/environment relative
    to the traffic it served in the same hour."""
    hints = []
    for kind, counts, total, minimum in (
        ("errors", window.errors, window.total_errors, MIN_ERRORS),
        ("slow requests", window.slow, window.total_slow, MIN_SLOW),
    ):
        if total < minimum or window.total_requests == 0:
            continue
        value, count = max(counts.items(), key=lambda kv: kv[1])
        share = count / total
        traffic_share = window.requests.get(value, 0) / window.total_requests
        if share >= MIN_SHARE and traffic_share > 0 and share / traffic_share >= MIN_TRAFFIC_LIFT:
            hints.append(Hint(
                dimension=dimension,
                value=value,
                text=(
                    f"{_pct(share)} of {kind} came from {dimension} {value}, "
                    f"which served {_pct(traffic_share)} of requests"
                ),
            ))
    return hints


def error_mix_hints(dimension: str, window: Breakdown, baseline: Breakdown) -> list[Hint]:
    """One error type / status code dominating the errors in a way it didn't
    in the previous week."""
    if window.total_errors < MIN_ERRORS:
        return []
    value, count = max(window.errors.items(), key=lambda kv: kv[1])
    share = count / window.total_errors
    if share < MIN_SHARE:
        return []
    label = "status" if dimension == "status_code" else dimension.replace("_", " ")
    base_total = baseline.total_errors
    base_share = baseline.errors.get(value, 0) / base_total if base_total else 0.0
    if base_share == 0:
        return [Hint(dimension, value,
                     f"{_pct(share)} of errors are {label} {value} (not seen in the previous 7 days)")]
    if share / base_share >= MIN_BASELINE_LIFT:
        return [Hint(dimension, value,
                     f"{_pct(share)} of errors are {label} {value} (vs {_pct(base_share)} in the previous 7 days)")]
    return []


_BREAKDOWN_SQL = """
SELECT coalesce({column}::text, '(none)') AS value,
       count(*) AS requests,
       count(*) FILTER (WHERE is_error) AS errors,
       count(*) FILTER (WHERE duration_ms > $5) AS slow
FROM request_events
WHERE service_name = $1 AND route = $2 AND time >= $3 AND time < $4
GROUP BY 1
"""

_BASELINE_P95_SQL = """
SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
FROM request_events
WHERE service_name = $1 AND route = $2 AND time >= $3 AND time < $4
"""


async def _breakdown(conn, column, service_name, route, start, end, slow_ms) -> Breakdown:
    rows = await conn.fetch(
        _BREAKDOWN_SQL.format(column=column), service_name, route, start, end, slow_ms
    )
    return Breakdown(
        requests={r["value"]: r["requests"] for r in rows},
        errors={r["value"]: r["errors"] for r in rows if r["errors"]},
        slow={r["value"]: r["slow"] for r in rows if r["slow"]},
    )


async def route_hints(
    conn: asyncpg.Connection, service_name: str, route: str, window_start: datetime
) -> list[Hint]:
    window_end = window_start + timedelta(hours=1)
    baseline_start = window_start - BASELINE_WINDOW
    # "Slow" means slower than the route's p95 over the previous week.
    slow_ms = await conn.fetchval(
        _BASELINE_P95_SQL, service_name, route, baseline_start, window_start
    )
    slow_ms = slow_ms if slow_ms is not None else float("inf")

    hints: list[Hint] = []
    for dimension in TRAFFIC_DIMENSIONS:
        window = await _breakdown(conn, dimension, service_name, route, window_start, window_end, slow_ms)
        if len(window.requests) > 1:  # one host can't be "concentrated"
            hints.extend(traffic_hints(dimension, window))
    for dimension in ERROR_DIMENSIONS:
        window = await _breakdown(conn, dimension, service_name, route, window_start, window_end, slow_ms)
        baseline = await _breakdown(conn, dimension, service_name, route, baseline_start, window_start, slow_ms)
        hints.extend(error_mix_hints(dimension, window, baseline))
    return hints


async def add_hints(pool: asyncpg.Pool, service_name: str, anomalies: list[dict]) -> None:
    """Adds a "hints" list (of {dimension, value, text}) to each anomaly dict
    in place. Anomalies need "route" and "window_start" (ISO 8601)."""
    if not anomalies:
        return
    async with pool.acquire() as conn:
        for anomaly in anomalies:
            hints = await route_hints(
                conn, service_name, anomaly["route"], datetime.fromisoformat(anomaly["window_start"])
            )
            anomaly["hints"] = [h.to_dict() for h in hints]
