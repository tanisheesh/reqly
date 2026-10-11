from __future__ import annotations

import logging

from .buffer import EventBuffer
from .capture import build_event
from .config import Config
from .openapi_push import OpenAPIPusher
from .request_context import ConsumerResolver
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
        self._openapi_pusher: OpenAPIPusher | None = None
        self._consumers: ConsumerResolver | None = None

        try:
            if config.consumer_header or config.consumer is not None:
                self._consumers = ConsumerResolver(
                    header=config.consumer_header,
                    func=config.consumer,
                    salt=config.consumer_salt,
                    hash_ids=config.hash_consumer,
                )
            self._sampler = Sampler(config.sample_rate)
            shipper = Shipper(
                collector_url=config.collector_url,
                api_key=config.api_key,
                service_name=config.service_name,
                sdk_version=config.sdk_version,
                release=config.release,
                environment=config.environment,
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
        if config.capture_request_body:
            logger.warning(
                "reqly: capture_request_body is ignored and will be removed: Reqly never "
                "records request bodies"
            )

    def record_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_ms: float,
        error: bool,
        error_type: str | None,
        request_bytes: int | None = None,
        response_bytes: int | None = None,
        request_info=None,
        llm: tuple[str, int, int] | None = None,
    ) -> None:
        """``request_info``: zero-argument callable returning a RequestInfo,
        only called when consumer tracking is on. ``llm``: (model,
        input_tokens, output_tokens) recorded during the request."""
        if self._disabled:
            return
        try:
            if self._openapi_pusher is not None:
                self._openapi_pusher.maybe_push()
            if route in self._ignore_routes:
                return
            if not self._sampler.should_sample():
                return
            event = build_event(
                consumer_id=self._consumer_id(request_info),
                llm=llm,
                service_name=self.config.service_name,
                method=method,
                route=route,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error,
                error_type=error_type,
                sdk_version=self.config.sdk_version,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
            )
            self._buffer.add(event)
        except Exception:
            logger.warning(
                "reqly: internal error, disabling instrumentation",
                exc_info=True,
            )
            self._disabled = True

    def _consumer_id(self, request_info) -> str | None:
        if self._consumers is None or request_info is None:
            return None
        try:
            return self._consumers.resolve(request_info)
        except Exception:
            # A failing consumer= callable costs the consumer id, not the event.
            logger.warning("reqly: consumer lookup failed", exc_info=True)
            return None

    def spec_source(self):
        """A callable returning the spec when push_openapi was given one (a
        dict, or a function returning it), else None (True: the framework's
        own spec, or nothing)."""
        spec = self.config.push_openapi
        if callable(spec):
            return spec
        if isinstance(spec, dict):
            return lambda: spec
        return None

    def enable_openapi_push(self, spec_factory) -> None:
        """Upload ``spec_factory()`` (the app's OpenAPI document) to the
        collector once, when the first request is recorded."""
        self._openapi_pusher = OpenAPIPusher(
            spec_factory=spec_factory,
            collector_url=self.config.collector_url,
            api_key=self.config.api_key,
            service_name=self.config.service_name,
        )

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
