import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import late_data
from app.db.late_data import LateDataTracker, refresh_windows

NOW = datetime(2026, 10, 9, 12, 30, tzinfo=timezone.utc)


def test_recent_events_are_not_tracked():
    t = LateDataTracker()
    t.note(NOW - timedelta(minutes=30), now=NOW)
    assert t.pending_since is None


def test_watermark_only_moves_backwards():
    t = LateDataTracker()
    t.note(NOW - timedelta(days=2), now=NOW)
    t.note(NOW - timedelta(days=1), now=NOW)
    assert t.pending_since == NOW - timedelta(days=2)
    t.note(NOW - timedelta(days=3), now=NOW)
    assert t.pending_since == NOW - timedelta(days=3)


def test_windows_are_hour_aligned_sliced_and_stop_at_end_offset():
    oldest = NOW - timedelta(days=10, minutes=17)
    windows = refresh_windows(oldest, NOW)
    by_view = {}
    for view, start, end in windows:
        by_view.setdefault(view, []).append((start, end))

    for view, end_offset in late_data.AGGREGATES:
        spans = by_view[view]
        assert spans[0][0] == oldest.replace(minute=0, second=0, microsecond=0)
        assert spans[-1][1] == NOW - end_offset
        for (s1, e1), (s2, _) in zip(spans, spans[1:]):
            assert e1 == s2  # contiguous, no gaps
        assert all(e - s <= late_data.REFRESH_SLICE for s, e in spans)
        assert len(spans) == 2  # ~10 days -> 7d + remainder


class _FailingConn:
    async def execute(self, *args):
        raise RuntimeError("db down")


class _FailingPool:
    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return _FailingConn()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def test_failed_refresh_restores_watermark():
    t = LateDataTracker()
    oldest = NOW - timedelta(days=2)
    t.note(oldest, now=NOW)
    with pytest.raises(RuntimeError):
        asyncio.run(t.refresh(_FailingPool(), now=NOW))
    assert t.pending_since == oldest
