"""Maps OTLP trace payloads to Reqly request_events rows.

Reqly is API-level, not a tracing backend: only SERVER spans that describe
an HTTP request become events. Client/internal/producer/consumer spans and
non-HTTP server spans (gRPC, messaging) are skipped by design.

LLM calls recorded with the OpenTelemetry GenAI conventions (client spans
with gen_ai.usage.input_tokens / output_tokens) are attributed to the HTTP
server span they ran under -- the nearest ancestor in the same export -- the
way the SDKs' record_llm_usage() attributes them to the current request.

Both wire formats are read into the same small span model (_Span) -- JSON
from its camelCase dict, protobuf straight from the parsed message, with no
detour through a dict -- so there is a single mapping path. Handles both the stable HTTP semantic conventions
(http.request.method, http.response.status_code) and the legacy names
(http.method, http.status_code) still emitted by older instrumentations.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
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


@dataclass(slots=True)
class _Span:
    is_server: bool
    trace_id: str  # lowercase hex
    span_id: str
    parent_id: str
    attrs: dict
    start_ns: object  # int from protobuf; a string (or anything) from JSON
    end_ns: object
    status_error: bool
    exception_type: object  # exception.type of the first "exception" event


@dataclass(slots=True)
class _Resource:
    attrs: dict
    spans: list[_Span]


def _json_exception_type(span: dict):
    for event in span.get("events") or []:
        if event.get("name") == "exception":
            return _attributes(event.get("attributes")).get("exception.type")
    return None


def _from_json(payload: dict) -> list[_Resource]:
    resources = []
    for resource_spans in payload.get("resourceSpans") or []:
        spans = [
            _Span(
                is_server=span.get("kind") in _SPAN_KIND_SERVER,
                trace_id=_hex_id(span.get("traceId")),
                span_id=_hex_id(span.get("spanId")),
                parent_id=_hex_id(span.get("parentSpanId")),
                attrs=_attributes(span.get("attributes")),
                start_ns=span.get("startTimeUnixNano"),
                end_ns=span.get("endTimeUnixNano"),
                status_error=(span.get("status") or {}).get("code") in _STATUS_CODE_ERROR,
                exception_type=_json_exception_type(span),
            )
            for scope_spans in resource_spans.get("scopeSpans") or []
            for span in scope_spans.get("spans") or []
        ]
        attrs = _attributes((resource_spans.get("resource") or {}).get("attributes"))
        resources.append(_Resource(attrs, spans))
    return resources


_PROTO_SCALARS = ("string_value", "bool_value", "int_value", "double_value")


def _proto_attributes(items) -> dict:
    out = {}
    for item in items:
        kind = item.value.WhichOneof("value")
        out[item.key] = getattr(item.value, kind) if kind in _PROTO_SCALARS else None
    return out


def _proto_exception_type(span):
    for event in span.events:
        if event.name == "exception":
            return _proto_attributes(event.attributes).get("exception.type")
    return None


def _from_proto(message) -> list[_Resource]:
    """message: a parsed ExportTraceServiceRequest."""
    resources = []
    for resource_spans in message.resource_spans:
        spans = [
            _Span(
                is_server=span.kind == 2,  # SPAN_KIND_SERVER
                trace_id=span.trace_id.hex(),
                span_id=span.span_id.hex(),
                parent_id=span.parent_span_id.hex(),
                attrs=_proto_attributes(span.attributes),
                start_ns=span.start_time_unix_nano,
                end_ns=span.end_time_unix_nano,
                status_error=span.status.code == 2,  # STATUS_CODE_ERROR
                exception_type=_proto_exception_type(span),
            )
            for scope_spans in resource_spans.scope_spans
            for span in scope_spans.spans
        ]
        resources.append(_Resource(_proto_attributes(resource_spans.resource.attributes), spans))
    return resources


def _first(attrs: dict, *names: str):
    for name in names:
        value = attrs.get(name)
        if value not in (None, ""):
            return value
    return None


def _hex_id(value: str | None) -> str:
    """OTLP/JSON encodes trace/span ids as hex; protobuf's own JSON printer
    (MessageToJson) produces base64. Normalize both to lowercase hex."""
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


_MAX_TOKENS = 10_000_000
_MAX_ANCESTRY = 64


def _is_http_server(span: _Span) -> bool:
    return span.is_server and (
        _first(span.attrs, "http.request.method", "http.method") is not None
        or _first(span.attrs, "http.response.status_code", "http.status_code") is not None
    )


def _genai_usage(attrs: dict) -> tuple[str, int, int] | None:
    """(model, input tokens, output tokens) of a GenAI span, or None."""
    tokens_in = _first(attrs, "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens")
    tokens_out = _first(attrs, "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens")
    if tokens_in is None and tokens_out is None:
        return None
    model = _first(attrs, "gen_ai.response.model", "gen_ai.request.model")
    if model is None:
        return None
    try:
        tokens_in, tokens_out = int(tokens_in or 0), int(tokens_out or 0)
    except (TypeError, ValueError):
        return None
    if not (0 <= tokens_in <= _MAX_TOKENS and 0 <= tokens_out <= _MAX_TOKENS):
        return None
    return _truncate(model, _MAX_SHORT_TEXT_LEN), tokens_in, tokens_out


def _llm_usage_by_server_span(resources: list[_Resource]) -> dict[tuple[str, str], dict]:
    """GenAI usage summed onto the HTTP server span each LLM call ran under:
    {(trace, span): {"llm_model", "llm_input_tokens", "llm_output_tokens"}}.
    Like the SDKs, all tokens are summed and the request is attributed to the
    model with the most tokens."""
    parents: dict[tuple[str, str], str] = {}
    servers: set[tuple[str, str]] = set()
    calls: list[tuple[tuple[str, str], tuple[str, int, int]]] = []
    for resource in resources:
        for span in resource.spans:
            key = (span.trace_id, span.span_id)
            parents[key] = span.parent_id
            if _is_http_server(span):
                servers.add(key)
            usage = _genai_usage(span.attrs)
            if usage is not None:
                calls.append((key, usage))

    per_server: dict[tuple[str, str], dict[str, list[int]]] = {}
    for key, (model, tokens_in, tokens_out) in calls:
        node, steps = key, 0
        while node not in servers and steps < _MAX_ANCESTRY:
            parent = parents.get(node)
            if not parent:
                break
            node, steps = (node[0], parent), steps + 1
        if node not in servers:
            continue  # its request isn't in this export (or there is none)
        totals = per_server.setdefault(node, {}).setdefault(model, [0, 0])
        totals[0] += tokens_in
        totals[1] += tokens_out

    out = {}
    for server, models in per_server.items():
        top = max(models, key=lambda m: sum(models[m]))
        out[server] = {
            "llm_model": top,
            "llm_input_tokens": min(sum(t[0] for t in models.values()), _MAX_TOKENS),
            "llm_output_tokens": min(sum(t[1] for t in models.values()), _MAX_TOKENS),
        }
    return out


def map_resource_spans(payload: dict, consumer_salt: str | None = None) -> MappingResult:
    """payload: an ExportTraceServiceRequest in OTLP/JSON shape."""
    return _map(_from_json(payload), consumer_salt)


def map_export_request(message, consumer_salt: str | None = None) -> MappingResult:
    """message: a parsed protobuf ExportTraceServiceRequest."""
    return _map(_from_proto(message), consumer_salt)


def _map(resources: list[_Resource], consumer_salt: str | None) -> MappingResult:
    result = MappingResult()
    llm_usage = _llm_usage_by_server_span(resources)
    for item in resources:
        resource = item.attrs
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

        for span in item.spans:
            _map_span(span, service_name, release, environment, host, result, llm_usage, consumer_salt)
    return result


def consumer_hash(value, salt: str) -> str:
    """The SDKs' consumer id: HMAC-SHA256 with the salt, first 16 hex digits,
    so one caller gets the same id whether it reaches Reqly via an SDK or OTLP."""
    return hmac.new(salt.encode(), str(value).encode(), hashlib.sha256).hexdigest()[:16]


def _consumer_id(attrs: dict, salt: str | None) -> str | None:
    """reqly.consumer_id or enduser.id, hashed. OTLP exporters send them in
    plain text, and a raw user id or email must never be stored: without
    REQLY_CONSUMER_SALT the consumer is dropped instead."""
    value = _first(attrs, "reqly.consumer_id", "enduser.id")
    if value is None or not salt:
        return None
    return consumer_hash(value, salt)


def _map_span(span: _Span, service_name, release, environment, host, result: MappingResult, llm_usage=None,
              consumer_salt: str | None = None) -> None:
    if not span.is_server:
        result.skipped += 1
        return

    attrs = span.attrs
    method = _first(attrs, "http.request.method", "http.method")
    status_code = _first(attrs, "http.response.status_code", "http.status_code")
    if method is None and status_code is None:
        result.skipped += 1  # a server span, but not HTTP (gRPC, messaging, ...)
        return

    span_id = f"{span.trace_id}:{span.span_id}"
    if method is None:
        result.reject(f"span {span_id}: missing http.request.method")
        return
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None

    try:
        start_ns = int(span.start_ns or 0)
        end_ns = int(span.end_ns or 0)
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

    span_status_error = span.status_error
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
        error_type = span.exception_type
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
            consumer_id=_consumer_id(attrs, consumer_salt),
            **(llm_usage or {}).get((span.trace_id, span.span_id), {}),
        )
    )


def _byte_count(attrs: dict, *names: str) -> int | None:
    value = _first(attrs, *names)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 10 * 1024**3 else None
