from __future__ import annotations

import logging

from .buffer import EventBuffer
from .capture import build_event
from .config import Config
from .sampling import Sampler
from .shipper import Shipper

logger = logging.getLogger("reqly")


class ReqlyClient:
    """Wires config + sampler + shipper + buffer together for one
    instrumented app, and enforces the SDK's core non-functional guarantee:
    instrumentation errors must NEVER propagate into the host application.

    Every public method here is internally guarded -- on the first internal
    failure, instrumentation disables itself (logs once, then becomes a
    no-op) rather than risk repeatedly raising into the host app's request
    path.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._disabled = False
        self._ignore_routes = set(config.ignore_routes)

        try:
            self._sampler = Sampler(config.sample_rate)
            shipper = Shipper(
                collector_url=config.collector_url,
                api_key=config.api_key,
                service_name=config.service_name,
                sdk_version=config.sdk_version,
            )
            self._buffer = EventBuffer(
                shipper=shipper,
                max_queue_size=config.max_queue_size,
                max_batch_size=config.max_batch_size,
                flush_interval_seconds=config.flush_interval_seconds,
            )
        except Exception:
            logger.warning(
                "reqly: failed to initialize, instrumentation disabled",
                exc_info=True,
            )
            self._disabled = True

    def record_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_ms: float,
        error: bool,
        error_type: str | None,
    ) -> None:
        if self._disabled:
            return
        try:
            if route in self._ignore_routes:
                return
            if not self._sampler.should_sample():
                return
            event = build_event(
                service_name=self.config.service_name,
                method=method,
                route=route,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error,
                error_type=error_type,
                sdk_version=self.config.sdk_version,
            )
            self._buffer.add(event)
        except Exception:
            logger.warning(
                "reqly: internal error, disabling instrumentation",
                exc_info=True,
            )
            self._disabled = True

    def stats(self) -> dict:
        if self._disabled:
            return {"disabled": True}
        return {"disabled": False, **self._buffer.stats(), **self._sampler.stats()}

    def shutdown(self) -> None:
        if self._disabled:
            return
        try:
            self._buffer.shutdown()
        except Exception:
            pass
