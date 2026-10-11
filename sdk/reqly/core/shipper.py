from __future__ import annotations

import logging
import random
import time

import httpx

from .capture import RequestEvent

logger = logging.getLogger("reqly")

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 0.5


def _is_retryable(status_code: int) -> bool:
    # 408/429 and any 5xx (collector restarting, proxy 502/503, transient DB
    # error) are worth retrying -- the collector dedups on event_id, so a
    # retry of a batch that actually landed can't double-count.
    return status_code in (408, 429) or status_code >= 500


class Shipper:
    """Ships batches of events to the collector over HTTP.

    Built on httpx with explicit per-phase timeouts so a slow or unreachable
    collector can NEVER block the host application's request path -- this
    client is only ever used from the buffer's background flush thread, never
    inline with a request.
    """

    def __init__(
        self,
        collector_url: str,
        api_key: str | None,
        service_name: str,
        sdk_version: str,
        release: str | None = None,
        environment: str | None = None,
    ) -> None:
        self._service_name = service_name
        self._sdk_version = sdk_version
        # Sent once per batch (ingest spec v2); collectors older than 0.3
        # ignore unknown top-level fields.
        self._batch_fields = {
            k: v for k, v in (("release", release), ("environment", environment)) if v
        }
        self.dropped_batches = 0
        self.shipped_events = 0

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-Reqly-Key"] = api_key

        self._base_url = collector_url.rstrip("/")
        self._headers = headers
        self._client = self._make_client()

    def _make_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=httpx.Timeout(connect=1.0, read=2.0, write=2.0, pool=1.0),
            http2=False,
        )

    def reset_after_fork(self) -> None:
        """Pooled sockets inherited from the parent process are shared with
        it, so a forked child must open its own connections."""
        self._client = self._make_client()

    def send_batch(self, events: list[RequestEvent], retries: int = _MAX_RETRIES) -> bool:
        """retries: attempts before the batch is dropped (1 at shutdown,
        where waiting out backoffs would hold up the app's exit)."""
        if not events:
            return True

        payload = {
            "service_name": self._service_name,
            "sdk_version": self._sdk_version,
            **self._batch_fields,
            "events": [e.to_dict() for e in events],
        }

        for attempt in range(retries):
            try:
                response = self._client.post("/v1/ingest", json=payload)
                if response.status_code < 400:
                    self.shipped_events += len(events)
                    return True
                if not _is_retryable(response.status_code):
                    # permanent client error (auth failure, bad payload, etc.) — no point retrying
                    logger.warning(
                        "reqly: collector rejected batch with %s, dropping",
                        response.status_code,
                    )
                    self.dropped_batches += 1
                    return False
                logger.debug(
                    "reqly: collector returned %s, attempt %d/%d",
                    response.status_code,
                    attempt + 1,
                    retries,
                )
            except httpx.HTTPError as exc:
                logger.debug(
                    "reqly: shipper error %s, attempt %d/%d",
                    exc,
                    attempt + 1,
                    retries,
                )
            except Exception as exc:
                logger.warning(
                    "reqly: unexpected shipper error %s, attempt %d/%d",
                    exc,
                    attempt + 1,
                    retries,
                )

            if attempt < retries - 1:
                backoff = _BACKOFF_BASE_SECONDS * (2**attempt)
                time.sleep(backoff + random.uniform(0, 0.1))

        self.dropped_batches += 1
        logger.warning(
            "reqly: dropped a batch of %d events after %d attempts",
            len(events),
            retries,
        )
        return False

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
