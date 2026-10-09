from __future__ import annotations

import math
import statistics
from statistics import NormalDist
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

# Deliberately simple and explainable: a z-score threshold against a
# day-of-week x hour-of-day seasonal baseline. This is NOT STL decomposition,
# Prophet, or an ML anomaly detector -- those need more historical data than
# a few weeks of demo traffic will have, and a transparent, auditable
# threshold is more defensible in an interview than a model that "just knows".
#
# A weekly run makes one comparison per (route, day-of-week, hour) cell --
# ~1,700 for a 10-route service. At z > 2 (~5% false positives per cell)
# that is dozens of pure-noise "anomalies" every week, so:
#   1. Error rates are tested on the actual request/error COUNTS: the chance
#      of seeing at least this many errors if the baseline rate still held
#      (exact Poisson tail -- the normal approximation badly understates how
#      often 3 errors in 30 requests happen by chance), reported as the
#      equivalent z-score. Only increases count -- the report is about what
#      degraded.
#   2. p95 has no count-based standard error, and the p95 of a few dozen
#      requests is essentially their 2nd-largest value -- pure noise. So p95
#      is only tested when every hour involved saw >= 100 requests, its
#      spread is floored at 20% of the baseline, and a shift must be >= 50%.
#   3. The z threshold is 4.0 -- roughly a Bonferroni correction for ~1,700
#      cells at alpha = 0.05, i.e. about one false positive every ~20 weeks.
#   4. A minimum effect size, so statistically "significant" but trivially
#      small shifts on huge volumes (e.g. +0.5pp of errors) are ignored.
#   5. A thin baseline is shrunk toward the route's overall error rate
#      (empirical Bayes): a cell whose few baseline weeks happened to see
#      1 error in 90 requests shouldn't make 5 in 30 look like an incident
#      when the route normally runs at 3%.
Z_THRESHOLD = 4.0
MIN_BASELINE_SAMPLES = 3
TOP_N_ANOMALIES = 5

MIN_BASELINE_ERROR_RATE = 0.005  # floor for the binomial variance, so a 0% baseline isn't infinitely tight
BASELINE_PRIOR_REQUESTS = 200  # pseudo-requests at the route-wide rate added to each cell's baseline
MIN_ERROR_RATE_DELTA = 0.02  # observed must differ from baseline by >= 2pp

MIN_REQUESTS_FOR_P95 = 100  # per hourly sample, recent and baseline alike
MIN_P95_STDDEV_FRACTION = 0.20  # p95 spread floor, as a fraction of the baseline mean
MIN_P95_RELATIVE_DELTA = 0.50  # p95 must differ by >= 50%

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
    # Start of the most recent hour in the anomalous (day, hour) slot, ISO
    # 8601 UTC -- lets later steps look at what was running at that time.
    window_start: str

    def to_dict(self) -> dict:
        return asdict(self)


def _pooled_error_rate(samples: list[dict]) -> tuple[float, int]:
    """(error_rate, request_count) summed over hourly samples. Pooling counts
    weights each hour by its traffic instead of averaging per-hour rates."""
    requests = sum(s["request_count"] for s in samples)
    if requests > 0:
        return sum(s["error_count"] for s in samples) / requests, requests
    return statistics.mean(s["error_rate"] for s in samples), 0


def _poisson_upper_tail(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam), summed directly from k upward so tiny
    probabilities keep their precision (1 - CDF would round to 0)."""
    log_term = -lam + k * math.log(lam) - math.lgamma(k + 1)
    total = 0.0
    i = k
    while log_term > -745 and i < k + 100_000:  # exp(-745) underflows to 0
        term = math.exp(log_term)
        total += term
        if term < total * 1e-15:
            break
        i += 1
        log_term += math.log(lam) - math.log(i)
    return min(total, 1.0)


def _error_rate_z(observed: float, n_observed: int, baseline: float) -> float:
    if n_observed <= 0 or observed - baseline < MIN_ERROR_RATE_DELTA:
        return 0.0
    expected = n_observed * max(baseline, MIN_BASELINE_ERROR_RATE)
    observed_errors = round(observed * n_observed)
    p_value = max(_poisson_upper_tail(observed_errors, expected), 1e-300)
    return -NormalDist().inv_cdf(p_value)


def _p95_z(recent_samples: list[dict], baseline_samples: list[dict]) -> float:
    if any(
        s["request_count"] < MIN_REQUESTS_FOR_P95 for s in recent_samples + baseline_samples
    ):
        return 0.0
    observed = statistics.mean(s["p95_ms"] for s in recent_samples)
    baseline_values = [s["p95_ms"] for s in baseline_samples]
    baseline_mean = statistics.mean(baseline_values)
    if baseline_mean <= 0:
        return 0.0
    delta = observed - baseline_mean  # increases only, like error rate
    if delta / baseline_mean < MIN_P95_RELATIVE_DELTA:
        return 0.0
    spread = max(statistics.pstdev(baseline_values), baseline_mean * MIN_P95_STDDEV_FRACTION)
    return delta / spread


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

    baseline_buckets: dict[tuple, list[dict]] = defaultdict(list)
    recent_buckets: dict[tuple, list[dict]] = defaultdict(list)

    for row in rows:
        bucket = row["bucket"]
        if bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        key = (row["route"], bucket.weekday(), bucket.hour)
        sample = {
            "bucket": bucket,
            "request_count": row.get("request_count") or 0,
            "error_count": row.get("error_count") or 0,
            "error_rate": row["error_rate"] or 0.0,
            "p95_ms": row["p95_ms"] or 0.0,
        }
        if bucket >= cutoff:
            recent_buckets[key].append(sample)
        else:
            baseline_buckets[key].append(sample)

    # Route-wide baseline error rate across all (day, hour) cells: the prior
    # each cell's own baseline is shrunk toward.
    route_baselines: dict[str, list[dict]] = defaultdict(list)
    for (route, _dow, _hour), samples in baseline_buckets.items():
        route_baselines[route].extend(samples)
    route_error_rate = {
        route: _pooled_error_rate(samples)[0] for route, samples in route_baselines.items()
    }

    anomalies: list[Anomaly] = []
    for key, recent_values in recent_buckets.items():
        baseline_values = baseline_buckets.get(key)
        # Require a minimum sample count before flagging -- otherwise mark
        # "insufficient data" implicitly by skipping, rather than risk a
        # false anomaly from a thin baseline.
        if not baseline_values or len(baseline_values) < MIN_BASELINE_SAMPLES:
            continue

        baseline_error_rate, baseline_requests = _pooled_error_rate(baseline_values)
        if baseline_requests > 0:
            prior = route_error_rate[key[0]]
            baseline_error_rate = (
                baseline_error_rate * baseline_requests + prior * BASELINE_PRIOR_REQUESTS
            ) / (baseline_requests + BASELINE_PRIOR_REQUESTS)
        observed_error_rate, observed_requests = _pooled_error_rate(recent_values)
        baseline_mean_p95 = statistics.mean(v["p95_ms"] for v in baseline_values)
        observed_p95 = statistics.mean(v["p95_ms"] for v in recent_values)

        z_score = max(
            _error_rate_z(observed_error_rate, observed_requests, baseline_error_rate),
            _p95_z(recent_values, baseline_values),
        )

        if z_score > Z_THRESHOLD:
            route, dow, hour = key
            anomalies.append(
                Anomaly(
                    route=route,
                    day_of_week=_DOW_NAMES[dow],
                    hour_range=f"{hour:02d}:00-{'00:00 (+1d)' if hour == 23 else f'{hour + 1:02d}:00'}",
                    observed_error_rate=round(observed_error_rate, 4),
                    baseline_error_rate=round(baseline_error_rate, 4),
                    observed_p95_ms=round(observed_p95, 1),
                    baseline_p95_ms=round(baseline_mean_p95, 1),
                    z_score=round(z_score, 2),
                    window_start=max(v["bucket"] for v in recent_values).isoformat(),
                )
            )

    anomalies.sort(key=lambda a: a.z_score, reverse=True)
    return anomalies[:TOP_N_ANOMALIES]
