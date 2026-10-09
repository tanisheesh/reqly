"""Maps OTLP trace payloads to Reqly request_events rows.

Reqly is API-level, not a tracing backend: only SERVER spans that describe
an HTTP request become events. Client/internal/producer/consumer spans and
non-HTTP server spans (gRPC, messaging) are skipped by design.

Works on the OTLP/JSON shape (camelCase keys). Protobuf payloads are
converted to the same shape with MessageToDict first, so there is a single
mapping path. Handles both the stable HTTP semantic conventions
(http.request.method, http.response.status_code) and the legacy names
(http.method, http.status_code) still emitted by older instrumentations.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..db.queries import event_row

# Namespace for event ids derived from (traceId, spanId): exporters retry
# failed exports, and a deterministic id lets the existing ON CONFLICT dedup
# drop the duplicates.
_SPAN_NAMESPACE = uuid.UUID("0b9a3c1e-7f4d-4e2a-9c6b-5d8e1f2a3b4c")

_SPAN_KIND_SERVER = {2, "2", "SPAN_KIND_SERVER"}
_STATUS_CODE_ERROR = {2, "2", "STATUS_CODE_ERROR"}

UNMATCHED_ROUTE = "__unmatched__"

# Limits mirror the /v1/ingest validation (routers/ingest.py).
_MAX_SERVICE_NAME_LEN = 128
_MAX_ROUTE_LEN = 512
_MAX_METHOD_LEN = 16
_MAX_SHORT_TEXT_LEN = 255
_MAX_RELEASE_LEN = 128
_MAX_ENVIRONMENT_LEN = 32
_MAX_DURATION_MS = 300_000


@dataclass
class MappingResult:
    rows: list[tuple] = field(default_factory=list)
    skipped: int = 0  # not HTTP server spans -- ignored by design, not an error
    rejected: int = 0  # HTTP server spans that failed validation
    reasons: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.rejected += 1
        if len(self.reasons) < 5:  # keep the error message short
            self.reasons.append(reason)


def _any_value(value: dict | None):
    """Unwraps an OTLP AnyValue ({"stringValue": "x"} etc.)."""
    if not value:
        return None
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:  # int64 is a JSON string in OTLP/JSON
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return None
    return None


def _attributes(items: list[dict] | None) -> dict:
    return {item.get("key"): _any_value(item.get("value")) for item in items or []}


def _first(attrs: dict, *names: str):
    for name in names:
        value = attrs.get(name)
        if value not in (None, ""):
            return value
    return None


def _hex_id(value: str | None) -> str:
    """OTLP/JSON encodes trace/span ids as hex; MessageToDict produces
    base64. Normalize both to lowercase hex."""
    if not value:
        return ""
    # Hex trace/span ids are exactly 32/16 chars; their base64 forms are
    # 24/12 chars, so length disambiguates ids made only of hex characters.
    if len(value) in (32, 16):
        try:
            int(value, 16)
            return value.lower()
        except ValueError:
            pass
    try:
        return base64.b64decode(value, validate=True).hex()
    except (binascii.Error, ValueError):
        return value


def _nanos_to_datetime(nanos: int) -> datetime:
    return datetime.fromtimestamp(nanos / 1e9, tz=timezone.utc)


def _truncate(value, limit: int) -> str | None:
    if value in (None, ""):
        return None
    return str(value)[:limit]


def map_resource_spans(payload: dict) -> MappingResult:
    """payload: an ExportTraceServiceRequest in OTLP/JSON shape."""
    result = MappingResult()
    for resource_spans in payload.get("resourceSpans") or []:
        resource = _attributes((resource_spans.get("resource") or {}).get("attributes"))
        service_name = _truncate(
            resource.get("service.name") or "unknown_service", _MAX_SERVICE_NAME_LEN
        )
        release = _truncate(resource.get("service.version"), _MAX_RELEASE_LEN)
        environment = _truncate(
            _first(resource, "deployment.environment.name", "deployment.environment"),
            _MAX_ENVIRONMENT_LEN,
        )
        host = _truncate(
            _first(resource, "k8s.pod.name", "host.name", "service.instance.id"),
            _MAX_SHORT_TEXT_LEN,
        )

        for scope_spans in resource_spans.get("scopeSpans") or []:
            for span in scope_spans.get("spans") or []:
                _map_span(span, service_name, release, environment, host, result)
    return result


def _map_span(span, service_name, release, environment, host, result: MappingResult) -> None:
    if span.get("kind") not in _SPAN_KIND_SERVER:
        result.skipped += 1
        return

    attrs = _attributes(span.get("attributes"))
    method = _first(attrs, "http.request.method", "http.method")
    status_code = _first(attrs, "http.response.status_code", "http.status_code")
    if method is None and status_code is None:
        result.skipped += 1  # a server span, but not HTTP (gRPC, messaging, ...)
        return

    span_id = f"{_hex_id(span.get('traceId'))}:{_hex_id(span.get('spanId'))}"
    if method is None:
        result.reject(f"span {span_id}: missing http.request.method")
        return
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None

    try:
        start_ns = int(span.get("startTimeUnixNano") or 0)
        end_ns = int(span.get("endTimeUnixNano") or 0)
    except (TypeError, ValueError):
        result.reject(f"span {span_id}: invalid timestamps")
        return
    if not start_ns or not end_ns:
        result.reject(f"span {span_id}: missing timestamps")
        return
    duration_ms = (end_ns - start_ns) / 1e6
    if duration_ms < 0 or duration_ms > _MAX_DURATION_MS:
        result.reject(f"span {span_id}: duration out of range")
        return

    span_status_error = (span.get("status") or {}).get("code") in _STATUS_CODE_ERROR
    if status_code is None:
        # No response was sent (connection dropped, unhandled crash before
        # headers). Count it the way the Python SDK does: as a 500.
        status_code = 500 if span_status_error else None
    if status_code is None or not 100 <= status_code <= 599:
        result.reject(f"span {span_id}: missing or invalid http.response.status_code")
        return

    error = status_code >= 500 or span_status_error
    error_type = attrs.get("error.type")
    if error_type is None:
        for event in span.get("events") or []:
            if event.get("name") == "exception":
                error_type = _attributes(event.get("attributes")).get("exception.type")
                break
    if error_type is not None and str(error_type).isdigit():
        error_type = None  # semconv sets error.type to the status code for HTTP errors

    route = _truncate(attrs.get("http.route"), _MAX_ROUTE_LEN) or UNMATCHED_ROUTE
    # The Express instrumentation reports http.route "/" for requests no
    # route matched (it comes from the always-on expressInit middleware), so
    # 404 scans would all pile onto "/". A static "/" route can only have
    # matched the path "/" itself, so any other path means nothing matched.
    if route == "/":
        path = _first(attrs, "url.path", "http.target")
        if path is not None and str(path).split("?", 1)[0] not in ("/", ""):
            route = UNMATCHED_ROUTE

    result.rows.append(
        event_row(
            event_id=str(uuid.uuid5(_SPAN_NAMESPACE, span_id)),
            time=_nanos_to_datetime(end_ns),
            service_name=service_name,
            method=str(method).upper()[:_MAX_METHOD_LEN],
            route=route,
            status_code=status_code,
            duration_ms=duration_ms,
            is_error=error,
            error_type=_truncate(error_type, _MAX_SHORT_TEXT_LEN),
            host=host,
            release=release,
            environment=environment,
            request_bytes=_byte_count(attrs, "http.request.body.size", "http.request_content_length"),
            response_bytes=_byte_count(attrs, "http.response.body.size", "http.response_content_length"),
        )
    )


def _byte_count(attrs: dict, *names: str) -> int | None:
    value = _first(attrs, *names)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 10 * 1024**3 else None
