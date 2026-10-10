import asyncio

from app.insights import scheduler as scheduler_module


def test_jobs_tolerate_a_busy_event_loop():
    """APScheduler's default misfire grace is 1 second: a run that starts
    later than that is skipped. Every job must allow a generous delay."""

    async def run():
        scheduler = scheduler_module.start_scheduler(weekly=True, hourly_alerts=True)
        try:
            return {job.id: job.misfire_grace_time for job in scheduler.get_jobs()}
        finally:
            scheduler.shutdown(wait=False)

    grace = asyncio.run(run())
    assert grace == {"weekly_insights": 6 * 3600, "slo_alerts": 240, "hourly_alerts": 2400}
