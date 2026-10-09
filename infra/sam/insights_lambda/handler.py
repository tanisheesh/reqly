"""
Reqly — Weekly Insights Lambda Handler

Entry point: lambda_handler(event, context)

Invoked by:
  - EventBridge Scheduler every Sunday 23:00 UTC  (production weekly run)
  - Lambda Function URL, POST with no body         (demo / live walkthrough)
  - sam local invoke InsightsFunction              (local testing)

This handler is intentionally self-contained: it does not import from the
collector package. Both the collector's APScheduler path and this Lambda path
implement the same pipeline against the same TimescaleDB schema and produce
identical output — the Lambda is an AWS-native alternative for the scheduled
job, not a replacement for the collector web tier.

Pipeline:
  TimescaleDB (route_errors_1hour, 8 weeks)
    → statistical anomaly detection (z-score vs day-of-week × hour-of-day
      seasonal baseline — pure Python/SQL, zero LLM involvement)
    → if anomalies: Groq (llama-3.3-70b-versatile) writes the report
    → if no anomalies: plain "nothing unusual this week" stored
    → save to insight_reports table (TimescaleDB)
    → archive to S3 as insights/{service}/{week}.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import NormalDist

import asyncpg
import boto3
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("reqly.insights")

DATABASE_URL: str = os.environ["DATABASE_URL"]
GROQ_API_KEY: str | None = os.environ.get("GROQ_API_KEY") or None
GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
S3_BUCKET: str | None = os.environ.get("S3_BUCKET") or None


# ---------------------------------------------------------------------------
# Anomaly detection
# Mirrors collector/app/insights/anomaly_detection.py exactly.
# ---------------------------------------------------------------------------

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
Z_THRESHOLD = 4.0
MIN_BASELINE_SAMPLES = 3
TOP_N_ANOMALIES = 5

MIN_BASELINE_ERROR_RATE = 0.005  # floor for the binomial variance, so a 0% baseline isn't infinitely tight
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
            "request_count": row.get("request_count") or 0,
            "error_count": row.get("error_count") or 0,
            "error_rate": row["error_rate"] or 0.0,
            "p95_ms": row["p95_ms"] or 0.0,
        }
        if bucket >= cutoff:
            recent_buckets[key].append(sample)
        else:
            baseline_buckets[key].append(sample)

    anomalies: list[Anomaly] = []
    for key, recent_values in recent_buckets.items():
        baseline_values = baseline_buckets.get(key)
        # Require a minimum sample count before flagging -- otherwise mark
        # "insufficient data" implicitly by skipping, rather than risk a
        # false anomaly from a thin baseline.
        if not baseline_values or len(baseline_values) < MIN_BASELINE_SAMPLES:
            continue

        baseline_error_rate, _ = _pooled_error_rate(baseline_values)
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
                )
            )

    anomalies.sort(key=lambda a: a.z_score, reverse=True)
    return anomalies[:TOP_N_ANOMALIES]


# ---------------------------------------------------------------------------
# Groq client
# Mirrors collector/app/insights/groq_client.py exactly.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a site-reliability analyst. You will be given pre-computed \
statistical anomalies for an API service's past week. Write a concise report \
(3-6 bullet points) explaining what was observed.

Rules:
- Only reference the numbers given. Do not invent root causes you cannot verify from the data.
- Phrase causal explanations as hypotheses ("likely due to", "consistent with"), never as \
asserted fact, and note explicitly that any causal explanation is a hypothesis worth \
investigating, not a confirmed diagnosis.
- If the anomalies list is empty, state plainly that no significant anomalies were found \
this week. Do not invent a problem to seem useful.
"""


def generate_report(service_name: str, week_start: str, anomalies: list[dict]) -> str:
    if not anomalies:
        return (
            f"No significant anomalies were found for **{service_name}** "
            f"for the week of {week_start}."
        )

    if not GROQ_API_KEY:
        return _fallback_report(service_name, week_start, anomalies)

    try:
        from groq import Groq

        client = Groq(api_key=GROQ_API_KEY, timeout=30.0)
        payload = {
            "service_name": service_name,
            "week_of": week_start,
            "anomalies": anomalies,
        }
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, indent=2)},
            ],
            temperature=0.3,
            max_tokens=600,
        )
        return response.choices[0].message.content
    except Exception:
        logger.exception("groq call failed — falling back to plain summary")
        return _fallback_report(service_name, week_start, anomalies)


def _fallback_report(service_name: str, week_start: str, anomalies: list[dict]) -> str:
    lines = [
        f"AI Insights for **{service_name}**, week of {week_start} "
        "(raw statistical findings — GROQ_API_KEY not configured or call failed):"
    ]
    for a in anomalies:
        lines.append(
            f"- `{a['route']}` on {a['day_of_week']} {a['hour_range']}: "
            f"error rate {a['observed_error_rate']:.1%} vs baseline "
            f"{a['baseline_error_rate']:.1%}; "
            f"p95 {a['observed_p95_ms']}ms vs baseline {a['baseline_p95_ms']}ms "
            f"(z-score {a['z_score']})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB helpers (asyncpg, no ORM)
# ---------------------------------------------------------------------------

async def list_services(pool: asyncpg.Pool) -> list[str]:
    rows = await pool.fetch(
        # Same source as the collector: the 90-day aggregate, not the 14-day
        # raw table, so services quiet for >2 weeks still get a report.
        "SELECT DISTINCT service_name FROM route_latency_1min ORDER BY service_name"
    )
    return [r["service_name"] for r in rows]


async def get_hourly_seasonal_data(pool: asyncpg.Pool, service_name: str) -> list[dict]:
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
                      report_text    = EXCLUDED.report_text,
                      generated_at   = now()
        """,
        service_name,
        week_start,
        anomalies_json,
        report_text,
    )


# ---------------------------------------------------------------------------
# S3 archive
# ---------------------------------------------------------------------------

def archive_to_s3(
    service_name: str,
    week_start: date,
    anomalies: list[dict],
    report_text: str,
) -> None:
    if not S3_BUCKET:
        return
    try:
        s3 = boto3.client("s3")
        key = f"insights/{service_name}/{week_start.isoformat()}.json"
        body = json.dumps(
            {
                "service_name": service_name,
                "week_start": week_start.isoformat(),
                "anomaly_count": len(anomalies),
                "anomalies": anomalies,
                "report_text": report_text,
            },
            indent=2,
        ).encode()
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType="application/json")
        logger.info("archived to s3://%s/%s", S3_BUCKET, key)
    except (BotoCoreError, ClientError):
        logger.exception("s3 archive failed (non-fatal — report already saved to DB)")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _current_week_start(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).date()


async def run_for_service(pool: asyncpg.Pool, service_name: str) -> dict:
    rows = await get_hourly_seasonal_data(pool, service_name)
    anomalies = detect_anomalies(rows)
    anomalies_dicts = [a.to_dict() for a in anomalies]
    week_start = _current_week_start()

    report_text = generate_report(service_name, week_start.isoformat(), anomalies_dicts)
    await save_insight_report(
        pool, service_name, week_start, json.dumps(anomalies_dicts), report_text
    )
    archive_to_s3(service_name, week_start, anomalies_dicts, report_text)

    logger.info(
        "done: service=%s week=%s anomalies=%d",
        service_name,
        week_start,
        len(anomalies_dicts),
    )
    return {
        "service_name": service_name,
        "week_start": week_start.isoformat(),
        "anomaly_count": len(anomalies_dicts),
    }


async def async_main() -> dict:
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=3)
    try:
        services = await list_services(pool)
        if not services:
            logger.warning("no services in route_latency_1min — nothing to process")
            return {"processed": 0, "services": []}

        results = []
        for service_name in services:
            try:
                result = await run_for_service(pool, service_name)
                results.append(result)
            except Exception:
                logger.exception("pipeline failed for service=%s", service_name)

        return {"processed": len(results), "services": results}
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """
    Compatible with:
      - EventBridge Scheduler  →  event is the scheduled event envelope
      - Lambda Function URL    →  event has requestContext.http
      - sam local invoke       →  event is {} or a custom payload
    """
    logger.info("invoked: %s", json.dumps(event, default=str))
    result = asyncio.run(async_main())
    logger.info("result: %s", json.dumps(result))
    return {"statusCode": 200, "body": json.dumps(result)}
