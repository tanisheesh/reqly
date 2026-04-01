from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

# Deliberately simple and explainable: a z-score threshold against a
# day-of-week x hour-of-day seasonal baseline. This is NOT STL decomposition,
# Prophet, or an ML anomaly detector -- those need more historical data than
# a few weeks of demo traffic will have, and a transparent, auditable
# threshold is more defensible in an interview than a model that "just knows".
Z_THRESHOLD = 2.0
MIN_BASELINE_SAMPLES = 3
TOP_N_ANOMALIES = 5

_DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class Anomaly:
    route: str
    day_of_week: str
    hour_range: str
    observed_error_rate: float
    baseline_error_rate: float
    observed_p95_ms: float
    baseline_p95_ms: float
    z_score: float

    def to_dict(self) -> dict:
        return asdict(self)


def detect_anomalies(rows: list[dict], now: datetime | None = None) -> list[Anomaly]:
    """rows: hourly (bucket, route, request_count, error_count, error_rate,
    p95_ms) records spanning ~8 trailing weeks, as returned by
    db.queries.get_hourly_seasonal_data.

    Splits rows into "baseline" (everything older than 7 days) and "recent"
    (the most recent 7 days), grouped by (route, day_of_week, hour_of_day) --
    this segmentation is exactly what makes a recurring pattern like "every
    Monday morning" visible; a flat rolling average would never surface it.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    baseline_buckets: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    recent_buckets: dict[tuple, list[tuple[float, float]]] = defaultdict(list)

    for row in rows:
        bucket = row["bucket"]
        if bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        key = (row["route"], bucket.weekday(), bucket.hour)
        error_rate = row["error_rate"] or 0.0
        p95_ms = row["p95_ms"] or 0.0
        if bucket >= cutoff:
            recent_buckets[key].append((error_rate, p95_ms))
        else:
            baseline_buckets[key].append((error_rate, p95_ms))

    anomalies: list[Anomaly] = []
    for key, recent_values in recent_buckets.items():
        baseline_values = baseline_buckets.get(key)
        # Require a minimum sample count before flagging -- otherwise mark
        # "insufficient data" implicitly by skipping, rather than risk a
        # false anomaly from a thin baseline.
        if not baseline_values or len(baseline_values) < MIN_BASELINE_SAMPLES:
            continue

        baseline_error_rates = [v[0] for v in baseline_values]
        baseline_p95s = [v[1] for v in baseline_values]
        baseline_mean_err = statistics.mean(baseline_error_rates)
        baseline_stddev_err = statistics.pstdev(baseline_error_rates) or 1e-9
        baseline_mean_p95 = statistics.mean(baseline_p95s)
        baseline_stddev_p95 = statistics.pstdev(baseline_p95s) or 1e-9

        observed_error_rate = statistics.mean(v[0] for v in recent_values)
        observed_p95 = statistics.mean(v[1] for v in recent_values)

        z_err = abs(observed_error_rate - baseline_mean_err) / baseline_stddev_err
        z_p95 = abs(observed_p95 - baseline_mean_p95) / baseline_stddev_p95
        z_score = max(z_err, z_p95)

        if z_score > Z_THRESHOLD:
            route, dow, hour = key
            anomalies.append(
                Anomaly(
                    route=route,
                    day_of_week=_DOW_NAMES[dow],
                    hour_range=f"{hour:02d}:00-{'00:00 (+1d)' if hour == 23 else f'{hour + 1:02d}:00'}",
                    observed_error_rate=round(observed_error_rate, 4),
                    baseline_error_rate=round(baseline_mean_err, 4),
                    observed_p95_ms=round(observed_p95, 1),
                    baseline_p95_ms=round(baseline_mean_p95, 1),
                    z_score=round(z_score, 2),
                )
            )

    anomalies.sort(key=lambda a: a.z_score, reverse=True)
    return anomalies[:TOP_N_ANOMALIES]
