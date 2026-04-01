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
import os
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

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
    """
    Splits 8 weeks of hourly data into:
      - baseline: everything older than 7 days (7 historical weeks)
      - recent:   the most recent 7 days

    Groups both by (route, day_of_week, hour_of_day). For each (dow, hour)
    bucket that appears in recent data, computes a z-score against the
    baseline mean + stddev. Flags buckets exceeding Z_THRESHOLD.

    Day-of-week × hour-of-day segmentation is exactly what makes a recurring
    pattern like "every Monday 08:00-10:00" visible — a flat rolling average
    would never surface it.
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
                    hour_range=f"{hour:02d}:00-{(hour + 1) % 24:02d}:00",
                    observed_error_rate=round(observed_error_rate, 4),
                    baseline_error_rate=round(baseline_mean_err, 4),
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

_SYSTEM_PROMPT = (
    "You are a site-reliability analyst. You will be given pre-computed "
    "statistical anomalies for an API service's past week. Write a concise "
    "report (3-6 bullet points) explaining what was observed.\n\n"
    "Rules:\n"
    "- Only reference the numbers given. Do not invent root causes you cannot verify.\n"
    "- Phrase causal explanations as hypotheses (\"likely due to\", \"consistent with\"), "
    "never as asserted fact.\n"
    "- If the anomalies list is empty, state plainly that no significant anomalies "
    "were found this week. Do not invent a problem to seem useful."
)


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

        client = Groq(api_key=GROQ_API_KEY)
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
        "SELECT DISTINCT service_name FROM request_events ORDER BY service_name"
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
            logger.warning("no services in request_events — nothing to process")
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
