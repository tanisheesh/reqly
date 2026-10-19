"""Client-error (4xx) spike alerts.

5xx stay the only errors in error rates and availability SLOs: a 4xx is the
API answering a bad request. But a sudden wave of 401 / 403 / 422 / 429 on a
route usually means something broke on one side of the contract -- auth
after a deploy, a frontend sending a field the backend no longer accepts, a
rate limit set too low. This compares each watched status code's share of a
route's requests in the last hour with the same weekday-hour of the previous
weeks, with the same exact Poisson tail as the error-rate alerts.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import NormalDist

import asyncpg

from ..insights.anomaly_detection import Z_THRESHOLD, _poisson_upper_tail
from ..insights.deploys import add_release_context
from ..traces import add_trace_ids
from . import actions, notifier
from .hourly import BASELINE_WEEKS, apply_detections

KIND = "client_errors"
DEFAULT_STATUSES = (401, 403, 422, 429)
MIN_COUNT = 20  # a "spike" of a handful of 401s is someone mistyping a key
MIN_SHARE_DELTA = 0.05  # the status must take >= 5pp more of the route's requests
MIN_RATIO = 2.0  # and at least double its usual share
MIN_BASELINE_HOURS = 3  # weekday-hours with traffic to compare against
MIN_BASELINE_SHARE = 0.001  # floor, so a status never seen before isn't infinitely unlikely

_DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_ROWS_SQL = """
SELECT bucket, route, status_code, count
FROM route_status_distribution_1hour
WHERE service_name = $1 AND bucket = ANY($2::timestamptz[])
"""


def detect_client_error_spikes(rows: list[dict], hour: datetime, statuses=DEFAULT_STATUSES) -> list[dict]:
    """rows: (bucket, route, status_code, count) for `hour` and the same hour
    of earlier weeks. One finding per route: its worst spiking status, with
    the others that spiked too."""
    watched = set(statuses)
    requests: dict[str, dict[datetime, int]] = defaultdict(lambda: defaultdict(int))
    counts: dict[tuple[str, int], dict[datetime, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        requests[r["route"]][r["bucket"]] += int(r["count"])
        if r["status_code"] in watched:
            counts[(r["route"], r["status_code"])][r["bucket"]] += int(r["count"])

    spikes: dict[str, list[dict]] = defaultdict(list)
    for (route, status), by_hour in counts.items():
        n = requests[route].get(hour, 0)
        k = by_hour.get(hour, 0)
        baseline_hours = [b for b, total in requests[route].items() if b != hour and total > 0]
        if k < MIN_COUNT or len(baseline_hours) < MIN_BASELINE_HOURS:
            continue
        baseline_n = sum(requests[route][b] for b in baseline_hours)
        baseline_k = sum(by_hour.get(b, 0) for b in baseline_hours)
        baseline_share = max(baseline_k / baseline_n, MIN_BASELINE_SHARE)
        share = k / n
        if share - baseline_share < MIN_SHARE_DELTA or share < MIN_RATIO * baseline_share:
            continue
        p_value = max(_poisson_upper_tail(k, n * baseline_share), 1e-300)
        z = -NormalDist().inv_cdf(p_value)
        if z < Z_THRESHOLD:
            continue
        spikes[route].append({
            "status_code": status,
            "observed_count": k,
            "expected_count": round(n * baseline_share, 1),
            "observed_share": round(share, 4),
            "baseline_share": round(baseline_share, 4),
            "z_score": round(z, 2) if math.isfinite(z) else 99.0,
        })

    findings = []
    end = hour + timedelta(hours=1)
    for route, found in spikes.items():
        found.sort(key=lambda s: (-s["z_score"], -s["observed_count"]))
        worst = found[0]
        findings.append({
            "kind": KIND,
            "route": route,
            **worst,
            "requests": requests[route][hour],
            "statuses": found,
            "day_of_week": _DOW_NAMES[hour.weekday()],
            "hour_range": f"{hour:%H:%M}-{end:%H:%M}",
            "window_start": hour.isoformat(),
        })
    findings.sort(key=lambda f: -f["z_score"])
    return findings


async def refresh_hour(pool: asyncpg.Pool, hour: datetime) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "CALL refresh_continuous_aggregate('route_status_distribution_1hour', $1::timestamptz, $2::timestamptz)",
            hour, hour + timedelta(hours=1),
        )


async def check_service(
    pool: asyncpg.Pool,
    service_name: str,
    hour: datetime,
    now: datetime,
    channels: notifier.Channels,
    renotify_after: timedelta,
    statuses=DEFAULT_STATUSES,
    enrich=None,
) -> list[tuple[str, dict]]:
    slots = [hour - timedelta(weeks=k) for k in range(BASELINE_WEEKS + 1)]
    rows = [dict(r) for r in await pool.fetch(_ROWS_SQL, service_name, slots)]
    findings = detect_client_error_spikes(rows, hour, statuses)
    if findings:
        # Was a new release behind it? The usual cause of a sudden 401/422 wave.
        await add_release_context(pool, service_name, findings)
        await add_trace_ids(pool, service_name, findings)
        for extra in enrich or ():
            await extra(pool, service_name, findings)
    events = await apply_detections(pool, service_name, hour, findings, now, renotify_after, kind=KIND)
    for event, alert in events:
        await actions.deliver(pool, channels, event, alert, now)
    return events
