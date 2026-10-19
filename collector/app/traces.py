"""Trace ids on request events: which ones are kept, and the few an alert
points at (see migrations/013_trace_ids.sql)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import asyncpg

from .config import settings

# W3C trace ids are 32 hex digits; Zipkin/B3 and some Jaeger setups use 16.
_TRACE_ID = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{32})$")
MAX_TRACES_PER_ALERT = 5


def normalize_trace_id(value) -> str | None:
    """Lowercase hex trace id, or None for anything else (an all-zero id is
    W3C's "invalid")."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if not _TRACE_ID.match(value) or not value.strip("0"):
        return None
    return value


def keep_trace_id(trace_id: str | None, status_code: int, duration_ms: float) -> str | None:
    """Only failed (4xx/5xx) and slow requests keep their trace id."""
    if trace_id is None:
        return None
    if status_code >= 400 or duration_ms >= settings.trace_id_slow_ms:
        return trace_id
    return None


def trace_url(trace_id: str) -> str | None:
    template = settings.trace_url_template
    return template.replace("{trace_id}", trace_id) if template else None


async def add_trace_ids(pool: asyncpg.Pool, service_name: str, findings: list[dict]) -> None:
    """Adds "trace_ids" (and "trace_urls" with TRACE_URL_TEMPLATE) to each
    alert finding: the hour's traces on its route, errors and the slowest
    first -- or, for a client error spike, responses with that status."""
    for finding in findings:
        start = datetime.fromisoformat(finding["window_start"])
        status = finding.get("status_code") if finding.get("kind") == "client_errors" else None
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (trace_id) trace_id, is_error, duration_ms
            FROM request_events
            WHERE service_name = $1 AND route = $2 AND time >= $3 AND time < $4 AND trace_id IS NOT NULL
              AND ($5::int IS NULL OR status_code = $5)
            ORDER BY trace_id, is_error DESC, duration_ms DESC
            """,
            service_name, finding["route"], start, start + timedelta(hours=1), status,
        )
        ranked = sorted(rows, key=lambda r: (not r["is_error"], -r["duration_ms"]))[:MAX_TRACES_PER_ALERT]
        finding["trace_ids"] = [r["trace_id"] for r in ranked]
        if settings.trace_url_template:
            finding["trace_urls"] = [trace_url(t) for t in finding["trace_ids"]]
