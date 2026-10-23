"""Reverse proxy access logs as request events: Reqly without touching the
app. Caddy's and Traefik's JSON access logs are read as they are; Nginx,
Envoy, HAProxy and anything else send JSON lines with the generic fields
below (a `log_format ... escape=json` for Nginx). Vector, Fluent Bit or
Promtail-style shippers forward the lines over HTTP.

Generic fields: method, path (or uri / request_uri), status, duration_ms or
request_time (seconds), time (ISO 8601 or epoch seconds), and optionally
host, bytes_sent / body_bytes_sent, request_length / bytes_received,
traceparent.

A proxy only knows the raw path (/users/42?x=1). Reqly never stores one: the
query string is dropped, and the path becomes a route template, from the
service's uploaded OpenAPI spec when it has one, else by collapsing id-like
segments (numbers, UUIDs, long hex or token-like strings) into {id}.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .openapi.drift import Operation, match_operation, normalize_path

_LINE_NAMESPACE = uuid.UUID("6a0d2b8e-91c4-4f53-8b7e-3f1e2d4c5a69")
MAX_LINES = 5000
_MAX_DURATION_MS = 300_000

_UUID = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")
_HEX = re.compile(r"^[0-9a-fA-F]{12,}$")
_NUMBER = re.compile(r"^\d+$")
_TOKEN = re.compile(r"^[A-Za-z0-9_\-=.~]{16,}$")


@dataclass
class LogRequest:
    method: str
    path: str  # raw, query string removed
    status: int
    duration_ms: float
    time: datetime  # when the request completed
    host: str | None = None
    request_bytes: int | None = None
    response_bytes: int | None = None
    traceparent: str | None = None


class LineError(ValueError):
    """A line that isn't an access log entry Reqly can use."""


def _int(value) -> int | None:
    try:
        return int(value) if value not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def _time(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value)):
        seconds = float(value)
        if seconds > 1e12:  # milliseconds (or finer)
            seconds /= 1e3 if seconds < 1e15 else 1e9
        return datetime.fromtimestamp(seconds, timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _header(headers, name: str) -> str | None:
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if key.lower() == name:
            return value[0] if isinstance(value, list) and value else value if isinstance(value, str) else None
    return None


def _strip_query(path: str) -> str:
    return path.split("?", 1)[0].split("#", 1)[0] or "/"


def parse_line(entry: dict[str, Any], received: datetime) -> LogRequest:
    """One access log entry (a parsed JSON object) of any supported format."""
    if isinstance(entry.get("request"), dict) and "status" in entry:  # Caddy
        request = entry["request"]
        method, path = request.get("method"), request.get("uri")
        status = _int(entry.get("status"))
        duration_s = _float(entry.get("duration"))
        duration = duration_s * 1000 if duration_s is not None else None
        time = _time(entry.get("ts"))
        if time is not None and duration is not None:
            time += timedelta(milliseconds=duration)  # Caddy logs when the request started
        host = request.get("host")
        request_bytes = _int(_header(request.get("headers"), "content-length"))
        response_bytes = _int(entry.get("size"))
        traceparent = _header(request.get("headers"), "traceparent")
    elif "RequestMethod" in entry or "DownstreamStatus" in entry:  # Traefik
        method, path = entry.get("RequestMethod"), entry.get("RequestPath")
        status = _int(entry.get("DownstreamStatus"))
        duration_ns = _float(entry.get("Duration"))
        duration = duration_ns / 1e6 if duration_ns is not None else None
        start = _time(entry.get("StartUTC") or entry.get("StartLocal"))
        time = start + timedelta(milliseconds=duration) if start and duration is not None else _time(entry.get("time"))
        host = entry.get("RequestHost")
        request_bytes = _int(entry.get("RequestContentSize"))
        response_bytes = _int(entry.get("DownstreamContentSize") or entry.get("OriginContentSize"))
        traceparent = entry.get("request_Traceparent") or entry.get("request_traceparent")
    else:  # Nginx / Envoy / HAProxy / anything with the generic fields
        method = entry.get("method") or entry.get("request_method")
        path = entry.get("path") or entry.get("uri") or entry.get("request_uri")
        status = _int(entry.get("status") or entry.get("response_code") or entry.get("status_code"))
        duration = _float(entry.get("duration_ms"))
        if duration is None and _float(entry.get("request_time")) is not None:
            duration = _float(entry.get("request_time")) * 1000  # Nginx: seconds
        if duration is None and _float(entry.get("duration")) is not None:
            duration = _float(entry.get("duration"))  # Envoy %DURATION%: milliseconds
        time = _time(entry.get("time") or entry.get("timestamp") or entry.get("time_iso8601") or entry.get("start_time"))
        host = entry.get("host") or entry.get("hostname") or entry.get("authority")
        request_bytes = _int(entry.get("request_length") or entry.get("bytes_received"))
        response_bytes = _int(entry.get("body_bytes_sent") or entry.get("bytes_sent"))
        traceparent = entry.get("traceparent") or entry.get("http_traceparent")

    if not isinstance(method, str) or not method or not isinstance(path, str) or not path:
        raise LineError("no method or path")
    if status is None or not 100 <= status <= 599:
        raise LineError("no or invalid status")
    if duration is None:
        raise LineError("no duration (log request_time / duration, see the docs)")
    if not 0 <= duration <= _MAX_DURATION_MS:
        raise LineError("duration out of range")
    return LogRequest(
        method=method.upper()[:16],
        path=_strip_query(path)[:2048],
        status=status,
        duration_ms=duration,
        time=time or received,
        host=str(host)[:255] if host else None,
        request_bytes=request_bytes,
        response_bytes=response_bytes,
        traceparent=traceparent if isinstance(traceparent, str) else None,
    )


def parse_body(body: bytes) -> list[Any]:
    """NDJSON (one object per line), or a JSON array of objects (Vector's
    http sink with codec json and framing newline_delimited or not)."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LineError(f"invalid JSON array: {exc.msg}")
        return data if isinstance(data, list) else [data]
    entries: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append(None)  # counted as rejected
    return entries


def _collapse(segment: str) -> str:
    if _NUMBER.match(segment) or _UUID.match(segment) or _HEX.match(segment):
        return "{id}"
    if _TOKEN.match(segment) and any(c.isdigit() for c in segment):
        return "{id}"
    return segment


def route_template(method: str, path: str, operations: list[Operation]) -> str:
    """The route for a raw path: the spec's path when one matches (this
    method first, then any), else the path with id-like segments as {id}."""
    if operations:
        op = match_operation(method, path, operations)
        if op is None:
            shape = normalize_path(path)
            op = next((o for o in operations if match_operation(o.method, shape, [o])), None)
        if op is not None:
            return op.path[:512]
    segments = [s for s in path.split("/") if s]
    return ("/" + "/".join(_collapse(s) for s in segments))[:512] if segments else "/"


def event_id(service: str, raw_line: str) -> str:
    """Stable per log line, so a shipper re-sending a batch adds nothing."""
    return str(uuid.uuid5(_LINE_NAMESPACE, f"{service}\n{raw_line}"))
