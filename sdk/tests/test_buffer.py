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

    def send_batch(self, events):
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
