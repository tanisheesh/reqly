"""Materializes late-arriving events into the continuous aggregates.

The aggregate refresh policies only look back a short window (1h for the
1-minute view, 3h for the hourly views). Events older than that -- the load
generator's history backfill, an SDK retrying after a long collector outage,
OTLP exporters flushing late -- land in request_events but would never reach
the aggregates the dashboard and the insights pipeline read from.

Ingest reports the oldest timestamp of every batch here; a background task
then refreshes the affected range of each aggregate explicitly.

Raw events are kept 14 days, the aggregates 90-180. Refreshing a range
whose raw chunks retention already dropped rebuilds it from whatever raw
rows exist now -- one stray old event would replace days of aggregate
history with that single event. So events older than MAX_EVENT_AGE are
rejected at ingest unless the batch is an explicit backfill (the sender
vouches for complete data, e.g. the load generator's history), and the
refresh never reaches further back than that for anything else.

A backfill rebuilds exactly the hours it contains, not the range from its
oldest event to now: between those hours lies other history, of every
service, whose raw rows are gone.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

logger = logging.getLogger("reqly.collector")

# Anything older than this is outside the shortest policy look-back
# (route_latency_1min: start_offset 1h), so the policies won't pick it up.
LATE_THRESHOLD = timedelta(hours=1)

# Raw retention is 14 days with 1-day chunks; a day of margin keeps refreshes
# inside raw data that still exists.
MAX_EVENT_AGE = timedelta(days=13)
# Clock skew tolerated for events stamped in the future.
MAX_FUTURE_SKEW = timedelta(minutes=15)


def event_time_problem(ts: datetime, now: datetime, backfill: bool = False) -> str | None:
    """Why an event's timestamp is refused, or None if it is fine."""
    if ts > now + MAX_FUTURE_SKEW:
        return "timestamp is in the future"
    if not backfill and ts < now - MAX_EVENT_AGE:
        return f"timestamp older than {MAX_EVENT_AGE.days} days (send it as a backfill batch)"
    return None


# Large backfills are refreshed in slices so a single CALL can't hold the
# aggregate's locks for minutes.
REFRESH_SLICE = timedelta(days=7)

# (view, end_offset of its refresh policy). Buckets newer than the end
# offset are left to the policy, matching how the views are defined.
# api_latency_1min only exists when the TimescaleDB Toolkit is installed
# (migration 003); views that don't exist are skipped.
AGGREGATES: tuple[tuple[str, timedelta], ...] = (
    ("route_latency_1min", timedelta(minutes=1)),
    ("route_errors_1hour", timedelta(hours=1)),
    ("route_status_distribution_1hour", timedelta(hours=1)),
    ("api_latency_1min", timedelta(minutes=1)),
    ("consumer_usage_1hour", timedelta(hours=1)),
    ("llm_usage_1hour", timedelta(hours=1)),
)


def _floor_to_hour(ts: datetime) -> datetime:
    # A refresh window only materializes buckets that lie fully inside it,
    # so the start is aligned down to the widest bucket (1 hour).
    return ts.replace(minute=0, second=0, microsecond=0)


def refresh_windows(
    oldest: datetime,
    now: datetime,
    aggregates: tuple[tuple[str, timedelta], ...] = AGGREGATES,
) -> list[tuple[str, datetime, datetime]]:
    """(view, start, end) refresh calls covering [oldest, now - end_offset],
    in REFRESH_SLICE-sized pieces. Pure function so it can be unit tested."""
    start = _floor_to_hour(oldest)
    windows = []
    for view, end_offset in aggregates:
        view_end = now - end_offset
        slice_start = start
        while slice_start < view_end:
            slice_end = min(slice_start + REFRESH_SLICE, view_end)
            windows.append((view, slice_start, slice_end))
            slice_start = slice_end
    return windows


def hour_runs(hours) -> list[tuple[datetime, datetime]]:
    """Hour starts -> [(start, end)] runs of consecutive hours."""
    runs: list[list[datetime]] = []
    for hour in sorted(set(hours)):
        if runs and runs[-1][1] == hour:
            runs[-1][1] = hour + timedelta(hours=1)
        else:
            runs.append([hour, hour + timedelta(hours=1)])
    return [(start, end) for start, end in runs]


def backfill_windows(
    hours,
    now: datetime,
    aggregates: tuple[tuple[str, timedelta], ...] = AGGREGATES,
) -> list[tuple[str, datetime, datetime]]:
    """(view, start, end) refresh calls covering exactly the given hours
    (cut at each view's end offset), in REFRESH_SLICE-sized pieces."""
    windows = []
    for run_start, run_end in hour_runs(hours):
        for view, end_offset in aggregates:
            view_end = min(run_end, now - end_offset)
            slice_start = run_start
            while slice_start < view_end:
                slice_end = min(slice_start + REFRESH_SLICE, view_end)
                windows.append((view, slice_start, slice_end))
                slice_start = slice_end
    return windows


class LateDataTracker:
    """Tracks the oldest late event seen since the last refresh. All access
    happens on the event loop thread, so no lock is needed: note() has no
    await, and refresh() swaps the watermark out before its first await."""

    def __init__(self) -> None:
        self._oldest: datetime | None = None
        self._backfill_hours: set[datetime] = set()

    @property
    def pending_since(self) -> datetime | None:
        return self._oldest

    def note_backfill(self, event_times, now: datetime | None = None) -> None:
        """The hours of a backfill batch, rebuilt exactly (see module doc)."""
        now = now or datetime.now(timezone.utc)
        self._backfill_hours.update(
            _floor_to_hour(t) for t in event_times if t < now - LATE_THRESHOLD
        )

    def note(self, oldest_event_time: datetime, now: datetime | None = None, backfill: bool = False) -> None:
        now = now or datetime.now(timezone.utc)
        if oldest_event_time >= now - LATE_THRESHOLD:
            return
        if not backfill:
            # Defense in depth for anything that skipped the ingest check.
            oldest_event_time = max(oldest_event_time, now - MAX_EVENT_AGE)
        if self._oldest is None or oldest_event_time < self._oldest:
            self._oldest = oldest_event_time

    async def refresh(self, pool: asyncpg.Pool, now: datetime | None = None) -> int:
        """Refreshes every aggregate over the pending range. Returns the
        number of refresh calls made. On failure the range is put back so
        the next tick retries it."""
        oldest, backfill_hours = self._oldest, self._backfill_hours
        if oldest is None and not backfill_hours:
            return 0
        self._oldest, self._backfill_hours = None, set()
        now = now or datetime.now(timezone.utc)

        started = asyncio.get_running_loop().time()
        windows: list = []
        try:
            async with pool.acquire() as conn:
                aggregates = await existing_aggregates(conn)
                if oldest is not None:
                    windows += refresh_windows(oldest, now, aggregates)
                windows += backfill_windows(backfill_hours, now, aggregates)
                for view, start, end in windows:
                    # CALL refresh_continuous_aggregate can't run inside a
                    # transaction block; a bare execute() is autocommit.
                    await conn.execute(
                        "CALL refresh_continuous_aggregate($1::regclass, $2::timestamptz, $3::timestamptz)",
                        view,
                        start,
                        end,
                    )
        except Exception:
            # put both back for the next tick (already vetted when first noted)
            if oldest is not None:
                self.note(oldest, now=now, backfill=True)
            self._backfill_hours |= backfill_hours
            raise

        logger.info(
            "Reqly collector: refreshed aggregates for late data since %s "
            "and %d backfilled hours (%d calls, %.1fs)",
            oldest.isoformat() if oldest else "-",
            len(backfill_hours),
            len(windows),
            asyncio.get_running_loop().time() - started,
        )
        return len(windows)


tracker = LateDataTracker()


async def existing_aggregates(conn: asyncpg.Connection) -> tuple[tuple[str, timedelta], ...]:
    present = []
    for view, end_offset in AGGREGATES:
        if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", view):
            present.append((view, end_offset))
    return tuple(present)


async def seed_empty_aggregates(pool: asyncpg.Pool, late: LateDataTracker = tracker) -> bool:
    """When a migration has just added an aggregate, it starts out empty
    (WITH NO DATA) and its refresh policy only looks back an hour. Queue a
    refresh from the oldest raw event so the new view covers the whole raw
    retention window. Returns True if a refresh was queued."""
    async with pool.acquire() as conn:
        for view, _ in await existing_aggregates(conn):
            if await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {view})"):
                continue
            oldest = await conn.fetchval("SELECT min(time) FROM request_events")
            if oldest is None:
                return False
            late.note(oldest, backfill=True)  # every raw row is still there
            logger.info(
                "Reqly collector: %s is empty; queued a refresh from %s", view, oldest.isoformat()
            )
            return True
    return False


async def run_refresh_loop(pool: asyncpg.Pool, interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await tracker.refresh(pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("late-data aggregate refresh failed; will retry")
