from __future__ import annotations

import atexit
import logging
import os
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

    Besides the interval, the thread is woken as soon as a full batch is
    queued, so heavy traffic is shipped as it comes instead of piling up
    between intervals (at 5 s and a 2,000-event queue, anything above ~400
    requests/s per process used to be dropped). A wake-up only sends full
    batches; the remainder waits for the interval, so a busy app never sends
    a stream of tiny batches.
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
        self._wake = threading.Event()
        self._start_thread()
        atexit.register(self.shutdown)
        # Pre-fork servers (gunicorn --preload, uWSGI without lazy-apps)
        # import the app -- and start this thread -- in the master, then
        # fork workers. Threads don't survive fork, so without this hook every
        # worker would queue events forever and never ship them.
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._reinit_after_fork)

    def _start_thread(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="reqly-flush", daemon=True
        )
        self._thread.start()

    def _reinit_after_fork(self) -> None:
        if self._stop_event.is_set():
            return
        # The parent may have held the lock mid-fork; a fresh one is safe
        # because the child is single-threaded at this point. Events queued
        # before the fork belong to the parent, which ships them itself.
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._queue.clear()
        self._shipper.reset_after_fork()
        self._start_thread()

    def add(self, event: RequestEvent) -> None:
        with self._lock:
            if len(self._queue) >= self._queue.maxlen:
                self._dropped_events += 1
            self._queue.append(event)
            if len(self._queue) >= self._max_batch_size:
                self._wake.set()

    def _drain_batch(self, full_only: bool = False) -> list[RequestEvent]:
        with self._lock:
            if full_only and len(self._queue) < self._max_batch_size:
                return []
            batch = []
            while self._queue and len(batch) < self._max_batch_size:
                batch.append(self._queue.popleft())
            return batch

    def _run(self) -> None:
        while not self._stop_event.is_set():
            woken = self._wake.wait(self._flush_interval_seconds)
            self._wake.clear()
            if self._stop_event.is_set():
                return
            self.flush(full_only=woken)

    def flush(self, full_only: bool = False) -> None:
        """Ships the queue. full_only: only whole batches (a wake-up on a
        full batch), leaving the remainder for the next interval."""
        try:
            while True:
                batch = self._drain_batch(full_only)
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
        self._wake.set()  # let the thread exit now instead of at the next interval
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
