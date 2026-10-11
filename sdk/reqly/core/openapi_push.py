from __future__ import annotations

import logging
import threading
from urllib.parse import quote
from typing import Callable

import httpx

logger = logging.getLogger("reqly")


class OpenAPIPusher:
    """Uploads the app's OpenAPI spec to the collector once per process, so
    Reqly can compare the documented API with the traffic it sees.

    The upload happens on the first recorded request rather than at
    instrument() time: apps often register routes after instrumenting, and
    by the time requests are served the spec is complete. It runs on a
    daemon thread and every failure is logged, never raised.
    """

    def __init__(
        self,
        *,
        spec_factory: Callable[[], dict],
        collector_url: str,
        api_key: str | None,
        service_name: str,
    ) -> None:
        self._spec_factory = spec_factory
        self._url = f"{collector_url.rstrip('/')}/v1/services/{quote(service_name, safe='')}/openapi"
        self._api_key = api_key
        self._started = False
        self._lock = threading.Lock()

    def maybe_push(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._push, name="reqly-openapi-push", daemon=True).start()

    def _push(self) -> None:
        try:
            spec = self._spec_factory()
            headers = {"X-Reqly-Key": self._api_key} if self._api_key else {}
            response = httpx.put(self._url, json=spec, headers=headers, timeout=10.0)
            if response.status_code == 200:
                logger.info("reqly: uploaded OpenAPI spec (%s operations)", response.json().get("operations"))
            else:
                logger.warning(
                    "reqly: OpenAPI spec upload failed: HTTP %s %s",
                    response.status_code, response.text[:200],
                )
        except Exception:
            logger.warning("reqly: OpenAPI spec upload failed", exc_info=True)
