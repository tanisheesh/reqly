from __future__ import annotations

import atexit
import logging
import threading
import time
from collections import deque

from .capture import RequestEvent
from .shipper import Shipper

logger = logging.getLogger("reqly")


class EventBuffer:
    """Bounded in-memory queue + background flush thread.

    A single daemon thread mechanism is used for both sync (Flask) and async
    (FastAPI) hosts so the core stays identical regardless of framework --
    deliberately simple over clever (no asyncio.create_task path for FastAPI).

    Backpressure: if the queue is full when a new event arrives, the oldest
    entry is dropped and a counter incremented. The calling request thread
    never blocks waiting for queue space.
    """

    def __init__(
        self,
        shipper: Shipper,
        max_queue_size: int = 2000,
        max_batch_size: int = 200,
        flush_interval_seconds: float = 5.0,
    ) -> None:
        self._shipper = shipper
        self._max_batch_size = max_batch_size
        self._flush_interval_seconds = flush_interval_seconds

        self._queue: deque[RequestEvent] = deque(maxlen=max_queue_size)
        self._lock = threading.Lock()
        self._dropped_events = 0

        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="reqly-flush", daemon=True
        )
        self._thread.start()
        atexit.register(self.shutdown)

    def add(self, event: RequestEvent) -> None:
        with self._lock:
            if len(self._queue) >= self._queue.maxlen:
                self._dropped_events += 1
            self._queue.append(event)

    def _drain_batch(self) -> list[RequestEvent]:
        with self._lock:
            batch = []
            while self._queue and len(batch) < self._max_batch_size:
                batch.append(self._queue.popleft())
            return batch

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._flush_interval_seconds)
            self.flush()

    def flush(self) -> None:
        try:
            while True:
                batch = self._drain_batch()
                if not batch:
                    return
                self._shipper.send_batch(batch)
                if len(batch) < self._max_batch_size:
                    return
        except Exception:
            logger.warning("reqly: unexpected error during flush", exc_info=True)

    def shutdown(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        try:
            self.flush()
        finally:
            self._shipper.close()

    def stats(self) -> dict:
        with self._lock:
            return {
                "queued_events": len(self._queue),
                "dropped_events": self._dropped_events,
                "shipped_events": self._shipper.shipped_events,
                "dropped_batches": self._shipper.dropped_batches,
            }
