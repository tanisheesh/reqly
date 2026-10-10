"""A stray event older than raw retention must not wipe aggregate history
(skipped when TimescaleDB is unreachable)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import late_data
from app.db import pool as pool_module
from app.db import queries
from tests.test_queries_db import _db_reachable, _refresh_all

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")


def _event(service, ts):
    return queries.event_row(
        event_id=str(uuid.uuid4()), time=ts, service_name=service, method="GET", route="/a",
        status_code=200, duration_ms=10.0, is_error=False, error_type=None, host="h",
    )


@pytest.mark.parametrize("backfill", [False, True])
def test_old_late_event_does_not_wipe_aggregates(backfill):
    """History 20 days back is in the aggregates but its raw chunks are gone
    (retention). One more event for that hour arrives. Refreshing that range
    would rebuild the hour from the one surviving raw event; without the
    backfill flag the refresh must stop at the raw-retention boundary."""
    service = f"test-age-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    hour = (now - timedelta(days=20)).replace(minute=0, second=0, microsecond=0)

    async def count(pool):
        return await pool.fetchval(
            "SELECT coalesce(sum(request_count), 0) FROM route_latency_1min WHERE service_name = $1", service
        )

    async def run():
        pool = await pool_module.create_pool()
        tracker = late_data.LateDataTracker()
        try:
            await queries.insert_events(pool, [_event(service, hour + timedelta(minutes=i)) for i in range(30)])
            await _refresh_all(pool)
            before = await count(pool)
            # retention drops the raw chunk; the aggregate keeps the history
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await queries.insert_events(pool, [_event(service, hour + timedelta(minutes=45))])
            tracker.note(hour + timedelta(minutes=45), now=now, backfill=backfill)
            await tracker.refresh(pool, now=now)
            return before, await count(pool)
        finally:
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            async with pool.acquire() as conn:
                for view, _ in await late_data.existing_aggregates(conn):
                    await conn.execute(
                        "CALL refresh_continuous_aggregate($1::regclass, $2::timestamptz, $3::timestamptz)",
                        view, hour, hour + timedelta(hours=1),
                    )
            await pool_module.close_pool()

    before, after = asyncio.run(run())
    assert before == 30
    if backfill:
        # the sender vouched for complete data: the hour is rebuilt from it
        assert after == 1
    else:
        assert after == 30  # history kept
