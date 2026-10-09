from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..db import queries
from ..db.pool import get_pool
from .anomaly_detection import detect_anomalies
from .deploys import add_release_context
from .hints import add_hints
from .groq_client import generate_report

logger = logging.getLogger("reqly.collector")


def _current_week_start(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).date()


async def run_insights_for_service(service_name: str) -> dict:
    """The full pipeline for one service: pull seasonal data, run pure
    statistical anomaly detection (zero LLM involvement), attach which
    release was running for each anomaly, then hand only the structured
    findings to Groq to write up. Used by both the weekly scheduled job and
    the manual /v1/insights/generate demo endpoint.
    """
    pool = get_pool()
    rows = await queries.get_hourly_seasonal_data(pool, service_name)
    anomalies = detect_anomalies(rows)
    anomalies_dicts = [a.to_dict() for a in anomalies]
    for enrich in (add_release_context, add_hints):
        try:
            await enrich(pool, service_name, anomalies_dicts)
        except Exception:
            # Release context and hints are enrichments; the report is still
            # worth producing without them.
            logger.exception("%s failed for service=%s", enrich.__name__, service_name)
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


async def run_hourly_alerts() -> None:
    from ..alerts import hourly, notifier
    from ..config import settings

    pool = get_pool()
    services = await queries.list_services(pool)
    channels = notifier.Channels(
        slack_webhook_url=settings.alert_slack_webhook_url,
        discord_webhook_url=settings.alert_discord_webhook_url,
        webhook_url=settings.alert_webhook_url,
        dashboard_url=settings.dashboard_url,
    )
    await hourly.run_hourly_check(
        pool, services, channels, timedelta(hours=settings.alert_renotify_hours)
    )


async def run_slo_alerts() -> None:
    from ..alerts import notifier, slo_alerts
    from ..config import settings

    channels = notifier.Channels(
        slack_webhook_url=settings.alert_slack_webhook_url,
        discord_webhook_url=settings.alert_discord_webhook_url,
        webhook_url=settings.alert_webhook_url,
        dashboard_url=settings.dashboard_url,
    )
    await slo_alerts.run_slo_check(get_pool(), channels, timedelta(hours=settings.alert_renotify_hours))


def start_scheduler(weekly: bool = True, hourly_alerts: bool = True) -> AsyncIOScheduler:
    """Weekly report: Sunday 23:00 UTC (the manual trigger endpoint exists
    so a live demo doesn't have to wait for it). Hourly alert check: :15
    past every hour, once the previous hour's data is in."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    if weekly:
        scheduler.add_job(
            run_insights_for_all_services,
            trigger="cron",
            day_of_week="sun",
            hour=23,
            minute=0,
            id="weekly_insights",
        )
    if hourly_alerts:
        # SLO burn rates: every 5 minutes (the fast-burn short window).
        scheduler.add_job(
            run_slo_alerts,
            trigger="cron",
            minute="*/5",
            id="slo_alerts",
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_hourly_alerts,
            trigger="cron",
            minute=15,
            id="hourly_alerts",
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    return scheduler
