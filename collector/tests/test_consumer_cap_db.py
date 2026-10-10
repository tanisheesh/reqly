"""Consumer cardinality cap against TimescaleDB (skipped when unreachable)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.consumers.cap import OTHER, ConsumerCap
from app.db import pool as pool_module
from app.db import queries
from tests.test_queries_db import _db_reachable

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

DAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=1)


def _event(service, consumer, t):
    return queries.event_row(
        event_id=str(uuid.uuid4()), time=t, service_name=service, method="GET", route="/x",
        status_code=200, duration_ms=1.0, is_error=False, error_type=None, host="h", consumer_id=consumer,
    )


def test_cap_keeps_the_first_n_consumers_per_day_and_survives_a_restart():
    service = f"test-cap-{uuid.uuid4().hex[:8]}"

    async def run():
        pool = await pool_module.create_pool()
        try:
            cap = ConsumerCap()
            batch = [(DAY, c) for c in ("a", "b", "c", "d", "a", None, "e")]
            first = await cap.apply(pool, service, batch, limit=3)
            again = await cap.apply(pool, service, [(DAY, "b"), (DAY, "f")], limit=3)
            next_day = await cap.apply(pool, service, [(DAY + timedelta(days=1), "f")], limit=3)
            off = await cap.apply(pool, service, [(DAY, "zzz")], limit=0)

            # a new process knows the day's consumers from the database
            await queries.insert_events(pool, [_event(service, c, DAY) for c in ("a", "b", "c")])
            restarted = await ConsumerCap().apply(pool, service, [(DAY, "c"), (DAY, "d")], limit=3)
            return first, again, next_day, off, restarted
        finally:
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool_module.close_pool()

    first, again, next_day, off, restarted = asyncio.run(run())
    assert first == ["a", "b", "c", OTHER, "a", None, OTHER]
    assert again == ["b", OTHER]
    assert next_day == ["f"]  # a new day starts empty
    assert off == ["zzz"]  # limit 0: no cap
    assert restarted == ["c", OTHER]
