from __future__ import annotations

import json
import logging
import os
import socket
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..consumers.queries import add_affected_consumers
from ..db import queries
from ..db.pool import get_pool
from ..openapi.store import drift_report
from .anomaly_detection import detect_anomalies
from .deploys import add_release_context
from .hints import add_hints
from .llm_report import generate_report
from .report import summarize_drift

logger = logging.getLogger("reqly.collector")


_INSTANCE = f"{socket.gethostname()}:{os.getpid()}"
_KEEP_RUNS = timedelta(days=30)


async def claim_run(pool, job: str, slot: datetime) -> bool:
    """True for exactly one collector per (job, slot): the one whose insert
    lands (migrations/014_scheduled_runs.sql)."""
    claimed = await pool.fetchval(
        """
        INSERT INTO scheduled_runs (job, slot, claimed_by) VALUES ($1, $2, $3)
        ON CONFLICT (job, slot) DO NOTHING RETURNING true
        """,
        job, slot, _INSTANCE,
    )
    if not claimed:
        logger.info("scheduled job %s for %s: another collector has it", job, slot.isoformat())
        return False
    await pool.execute("DELETE FROM scheduled_runs WHERE job = $1 AND slot < $2", job, slot - _KEEP_RUNS)
    return True


def _five_minutes(now: datetime) -> datetime:
    return now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)


def _current_week_start(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=now.weekday())).date()


async def run_insights_for_service(service_name: str) -> dict:
    """The full pipeline for one service: pull seasonal data, run pure
    statistical anomaly detection (zero LLM involvement), attach which
    release was running for each anomaly, then hand only the structured
    findings to the LLM to write up. Used by both the weekly scheduled job and
    the manual /v1/insights/generate demo endpoint.
    """
    pool = get_pool()
    rows = await queries.get_hourly_seasonal_data(pool, service_name)
    anomalies = detect_anomalies(rows)
    anomalies_dicts = [a.to_dict() for a in anomalies]
    for enrich in (add_release_context, add_hints, add_affected_consumers):
        try:
            await enrich(pool, service_name, anomalies_dicts)
        except Exception:
            # Release context and hints are enrichments; the report is still
            # worth producing without them.
            logger.exception("%s failed for service=%s", enrich.__name__, service_name)
    week_start = _current_week_start()

    drift = None
    try:
        drift = summarize_drift(await drift_report(pool, service_name))
    except Exception:
        logger.exception("API drift summary failed for service=%s", service_name)

    report_text = await generate_report(service_name, week_start.isoformat(), anomalies_dicts, drift)

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
    week = datetime.combine(_current_week_start(), datetime.min.time(), tzinfo=timezone.utc)
    if not await claim_run(pool, "weekly_insights", week):
        return
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
    now = datetime.now(timezone.utc)
    if not await claim_run(pool, "hourly_alerts", hourly.last_complete_hour(now)):
        return
    services = await queries.list_services(pool)
    channels = notifier.channels_from_settings(settings)
    await hourly.run_hourly_check(
        pool, services, channels, timedelta(hours=settings.alert_renotify_hours), now=now,
        llm_cost_min_usd=settings.llm_cost_alert_min_usd,
        client_error_statuses=settings.client_error_alert_statuses,
    )


async def run_slo_alerts() -> None:
    from ..alerts import notifier, slo_alerts
    from ..config import settings

    pool = get_pool()
    now = datetime.now(timezone.utc)
    if not await claim_run(pool, "slo_alerts", _five_minutes(now)):
        return
    channels = notifier.channels_from_settings(settings)
    await slo_alerts.run_slo_check(pool, channels, timedelta(hours=settings.alert_renotify_hours), now=now)


def start_scheduler(weekly: bool = True, hourly_alerts: bool = True) -> AsyncIOScheduler:
    """Weekly report: Sunday 23:00 UTC (the manual trigger endpoint exists
    so a live demo doesn't have to wait for it). Hourly alert check: :15
    past every hour, once the previous hour's data is in."""
    # APScheduler drops a run that starts more than misfire_grace_time late
    # (default: 1 second). Everything here shares one event loop with ingest,
    # so a busy second would silently skip a week's report or an hour's
    # alerts; each job may start late up to its own period's worth.
    scheduler = AsyncIOScheduler(timezone="UTC")
    if weekly:
        scheduler.add_job(
            run_insights_for_all_services,
            trigger="cron",
            day_of_week="sun",
            hour=23,
            minute=0,
            id="weekly_insights",
            misfire_grace_time=6 * 3600,
            coalesce=True,
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
            misfire_grace_time=4 * 60,
        )
        scheduler.add_job(
            run_hourly_alerts,
            trigger="cron",
            minute=15,
            id="hourly_alerts",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=40 * 60,
        )
    scheduler.start()
    return scheduler
