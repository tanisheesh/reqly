from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..db import queries
from ..db.pool import get_pool
from .anomaly_detection import detect_anomalies
from .groq_client import generate_report

logger = logging.getLogger("reqly.collector")


def _current_week_start(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).date()


async def run_insights_for_service(service_name: str) -> dict:
    """The full pipeline for one service: pull seasonal data, run pure
    statistical anomaly detection (zero LLM involvement), then hand only
    the structured findings to Groq to write up. Used by both the weekly
    scheduled job and the manual /v1/insights/generate demo endpoint.
    """
    pool = get_pool()
    rows = await queries.get_hourly_seasonal_data(pool, service_name)
    anomalies = detect_anomalies(rows)
    anomalies_dicts = [a.to_dict() for a in anomalies]
    week_start = _current_week_start()

    report_text = await generate_report(service_name, week_start.isoformat(), anomalies_dicts)

    await queries.save_insight_report(
        pool, service_name, week_start, json.dumps(anomalies_dicts), report_text
    )
    return {
        "service_name": service_name,
        "week_start": week_start.isoformat(),
        "anomalies_json": anomalies_dicts,
        "report_text": report_text,
    }


async def run_insights_for_all_services() -> None:
    pool = get_pool()
    services = await queries.list_services(pool)
    for service_name in services:
        try:
            await run_insights_for_service(service_name)
        except Exception:
            logger.exception("insights generation failed for service=%s", service_name)


def start_scheduler() -> AsyncIOScheduler:
    """Runs Sunday 23:00 UTC. The manual trigger endpoint exists precisely
    so a live demo doesn't require waiting for this to fire.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_insights_for_all_services,
        trigger="cron",
        day_of_week="sun",
        hour=23,
        minute=0,
        id="weekly_insights",
    )
    scheduler.start()
    return scheduler
