"""The incoming request's trace id, for trace correlation in alerts: W3C
``traceparent``, then Sentry's ``sentry-trace``, then Zipkin's
``x-b3-traceid``. Only read for failed (4xx/5xx) or slow requests -- the
collector keeps no other -- so a fast 200 never pays for it."""

from __future__ import annotations

import re
from typing import Mapping

SLOW_MS = 500.0  # the collector keeps 1s+ by default (TRACE_ID_SLOW_MS)

_HEX = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{32})$")


def _valid(value: str) -> str | None:
    value = value.strip().lower()
    return value if _HEX.match(value) and value.strip("0") else None


def trace_id_from_headers(headers: Mapping[str, str]) -> str | None:
    """headers: lower-case names."""
    parent = headers.get("traceparent")
    if parent:
        parts = parent.split("-")  # version-traceid-parentid-flags
        if len(parts) >= 4 and len(parts[1]) == 32:
            return _valid(parts[1])
    sentry = headers.get("sentry-trace")
    if sentry:
        found = _valid(sentry.split("-")[0])
        if found:
            return found
    b3 = headers.get("x-b3-traceid")
    return _valid(b3) if b3 else None


def wants_trace_id(status_code: int, duration_ms: float) -> bool:
    return status_code >= 400 or duration_ms >= SLOW_MS
