"""
Reqly — Weekly Insights Lambda Handler

Entry point: lambda_handler(event, context)

Invoked by:
  - EventBridge Scheduler every Sunday 23:00 UTC  (production weekly run)
  - Lambda Function URL, POST with no body         (demo stacks only)
  - sam local invoke InsightsFunction              (local testing)

The detection, deploy-attribution and report-text logic is NOT reimplemented
here: reqly_insights/ holds byte-identical copies of the collector's
app/insights modules (regenerate with `python infra/sam/sync_insights.py`;
collector/tests/test_lambda_parity.py fails CI if they go stale). This file
only adds the Lambda-specific parts: config from env, the Groq call, DB
reads/writes and the S3 archive.

Pipeline:
  TimescaleDB (route_errors_1hour, 8 weeks)
    → statistical anomaly detection (reqly_insights.anomaly_detection)
    → release context per anomaly (reqly_insights.deploys)
    → if anomalies: Groq writes the report (prompt: reqly_insights.report)
    → save to insight_reports (TimescaleDB) → archive to S3
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

import asyncpg
import boto3
from botocore.exceptions import BotoCoreError, ClientError

from reqly_insights.anomaly_detection import detect_anomalies
from reqly_insights.deploys import add_release_context
from reqly_insights.hints import add_hints
from reqly_insights.report import SYSTEM_PROMPT, fallback_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("reqly.insights")

DATABASE_URL: str = os.environ["DATABASE_URL"]
GROQ_API_KEY: str | None = os.environ.get("GROQ_API_KEY") or None
GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
S3_BUCKET: str | None = os.environ.get("S3_BUCKET") or None


# ---------------------------------------------------------------------------
# Report text (same prompt and fallback as the collector's groq_client)
# ---------------------------------------------------------------------------

def generate_report(service_name: str, week_start: str, anomalies: list[dict]) -> str:
    if not anomalies:
        return (
            f"No significant anomalies were found for **{service_name}** "
            f"for the week of {week_start}."
        )

    if not GROQ_API_KEY:
        return fallback_report(service_name, week_start, anomalies)

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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, indent=2)},
            ],
            temperature=0.3,
            max_tokens=600,
        )
        return response.choices[0].message.content
    except Exception:
        logger.exception("groq call failed — falling back to plain summary")
        return fallback_report(service_name, week_start, anomalies)


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
    for enrich in (add_release_context, add_hints):
        try:
            await enrich(pool, service_name, anomalies_dicts)
        except Exception:
            logger.exception("%s failed for service=%s", enrich.__name__, service_name)
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
