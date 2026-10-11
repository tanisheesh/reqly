import threading
import time

from reqly.core.buffer import EventBuffer
from reqly.core.capture import RequestEvent


class FakeShipper:
    def __init__(self):
        self.batches = []
        self.dropped_batches = 0
        self.shipped_events = 0
        self._lock = threading.Lock()

    def send_batch(self, events, retries=3):
        with self._lock:
            self.batches.append(list(events))
            self.shipped_events += len(events)
        return True

    def close(self):
        pass


def _event(i: int) -> RequestEvent:
    return RequestEvent(route=f"/r{i}")


def test_flush_drains_queue_in_batches():
    shipper = FakeShipper()
    buf = EventBuffer(
        shipper=shipper, max_queue_size=100, max_batch_size=10, flush_interval_seconds=999
    )
    try:
        for i in range(25):
            buf.add(_event(i))
        buf.flush()
        assert shipper.shipped_events == 25
        assert len(shipper.batches) == 3  # 10 + 10 + 5
    finally:
        buf.shutdown()


def test_backpressure_drops_oldest_when_queue_full():
    shipper = FakeShipper()
    buf = EventBuffer(
        shipper=shipper, max_queue_size=5, max_batch_size=10, flush_interval_seconds=999
    )
    try:
        for i in range(8):
            buf.add(_event(i))
        stats = buf.stats()
        assert stats["queued_events"] == 5
        assert stats["dropped_events"] == 3
    finally:
        buf.shutdown()


def test_background_thread_flushes_on_interval():
    shipper = FakeShipper()
    buf = EventBuffer(
        shipper=shipper, max_queue_size=100, max_batch_size=10, flush_interval_seconds=0.05
    )
    try:
        buf.add(_event(1))
        time.sleep(0.3)
        assert shipper.shipped_events >= 1
    finally:
        buf.shutdown()


def test_reinit_after_fork_restarts_flush_thread_and_drops_parent_events():
    shipper = FakeShipper()
    shipper.reset_after_fork = lambda: None
    buf = EventBuffer(
        shipper=shipper, max_queue_size=100, max_batch_size=10, flush_interval_seconds=999
    )
    try:
        buf.add(_event(1))  # queued in the "parent" before the fork
        old_thread = buf._thread
        buf._reinit_after_fork()
        assert buf._thread is not old_thread
        assert buf._thread.is_alive()
        assert buf.stats()["queued_events"] == 0
        buf.add(_event(2))
        buf.flush()
        assert [e.route for b in shipper.batches for e in b] == ["/r2"]
    finally:
        buf.shutdown()


def test_a_full_batch_is_shipped_without_waiting_for_the_interval():
    shipper = FakeShipper()
    buf = EventBuffer(
        shipper=shipper, max_queue_size=1000, max_batch_size=10, flush_interval_seconds=999
    )
    try:
        for i in range(25):
            buf.add(_event(i))
        deadline = time.monotonic() + 2
        while shipper.shipped_events < 20 and time.monotonic() < deadline:
            time.sleep(0.01)
        # the two full batches went out at once; the 5 left wait for the interval
        assert [len(b) for b in shipper.batches] == [10, 10]
        assert buf.stats()["queued_events"] == 5
    finally:
        buf.shutdown()
    assert shipper.shipped_events == 25  # shutdown sends the rest


def test_heavy_traffic_is_not_dropped_between_intervals():
    # 5,000 events against a 1,000-event queue: before eager flushing, 4,000
    # would have been dropped while waiting for the interval.
    shipper = FakeShipper()
    buf = EventBuffer(
        shipper=shipper, max_queue_size=1000, max_batch_size=100, flush_interval_seconds=999
    )
    try:
        for i in range(5000):
            buf.add(_event(i))
            if i % 500 == 0:
                time.sleep(0.01)  # let the flush thread run, as request handling would
        buf.flush()
        assert buf.stats()["dropped_events"] == 0
        assert shipper.shipped_events == 5000
    finally:
        buf.shutdown()


class DownShipper(FakeShipper):
    def send_batch(self, events, retries=3):
        self.batches.append(len(events))
        return False


def test_shutdown_ships_everything_when_the_collector_is_up():
    shipper = FakeShipper()
    buf = EventBuffer(shipper=shipper, max_queue_size=100, max_batch_size=10, flush_interval_seconds=999)
    for i in range(25):
        buf.add(_event(i))
    buf.shutdown()
    assert shipper.shipped_events == 25 and buf.stats()["dropped_events"] == 0


def test_shutdown_gives_up_at_the_first_failure():
    shipper = DownShipper()
    buf = EventBuffer(shipper=shipper, max_queue_size=100, max_batch_size=10, flush_interval_seconds=999)
    buf._queue.extend(_event(i) for i in range(25))  # without waking the flush thread
    started = time.monotonic()
    buf.shutdown()
    assert time.monotonic() - started < 1
    assert shipper.batches == [10]  # one attempt, then stop
    assert buf.stats()["dropped_events"] == 15  # the rest, counted
