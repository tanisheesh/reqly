from __future__ import annotations

import logging
import random
import time

import httpx

from .capture import RequestEvent

logger = logging.getLogger("reqly")

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 0.5


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
    ) -> None:
        self._service_name = service_name
        self._sdk_version = sdk_version
        self.dropped_batches = 0
        self.shipped_events = 0

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-Reqly-Key"] = api_key

        self._client = httpx.Client(
            base_url=collector_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(connect=1.0, read=2.0, write=2.0, pool=1.0),
            http2=False,
        )

    def send_batch(self, events: list[RequestEvent]) -> bool:
        if not events:
            return True

        payload = {
            "service_name": self._service_name,
            "sdk_version": self._sdk_version,
            "events": [e.to_dict() for e in events],
        }

        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.post("/v1/ingest", json=payload)
                if response.status_code < 400:
                    self.shipped_events += len(events)
                    return True
                if response.status_code != 429:
                    # permanent client error (auth failure, bad payload, etc.) — no point retrying
                    logger.warning(
                        "reqly: collector rejected batch with %s, dropping",
                        response.status_code,
                    )
                    self.dropped_batches += 1
                    return False
                logger.debug(
                    "reqly: rate-limited (429), attempt %d/%d",
                    attempt + 1,
                    _MAX_RETRIES,
                )
            except httpx.HTTPError as exc:
                logger.debug(
                    "reqly: shipper error %s, attempt %d/%d",
                    exc,
                    attempt + 1,
                    _MAX_RETRIES,
                )
            except Exception as exc:
                logger.warning(
                    "reqly: unexpected shipper error %s, attempt %d/%d",
                    exc,
                    attempt + 1,
                    _MAX_RETRIES,
                )

            if attempt < _MAX_RETRIES - 1:
                backoff = _BACKOFF_BASE_SECONDS * (2**attempt)
                time.sleep(backoff + random.uniform(0, 0.1))

        self.dropped_batches += 1
        logger.warning(
            "reqly: dropped a batch of %d events after %d retries",
            len(events),
            _MAX_RETRIES,
        )
        return False

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
