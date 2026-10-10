"""OTLP -> request_events mapping. The fixtures are real exports (sanitized:
host/process attributes and stack traces removed):

- express_http_json.json / express_http_protobuf.bin: Node 26, Express 4,
  @opentelemetry/auto-instrumentations-node, stable HTTP semconv.
- fastapi_python_legacy.bin: opentelemetry-instrumentation-fastapi with its
  default (legacy) attribute names.
"""

import json
from pathlib import Path

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from app.db.queries import EVENT_COLUMNS
from app.otlp.mapping import UNMATCHED_ROUTE, map_resource_spans

FIXTURES = Path(__file__).parent / "fixtures" / "otlp"


def _proto_fixture(name):
    message = ExportTraceServiceRequest()
    message.ParseFromString((FIXTURES / name).read_bytes())
    return MessageToDict(message)


def _events(result):
    rows = [dict(zip(EVENT_COLUMNS, row)) for row in result.rows]
    return sorted(rows, key=lambda r: (r["route"], r["status_code"]))


def _summary(result):
    return [
        (e["method"], e["route"], e["status_code"], e["is_error"], e["error_type"])
        for e in _events(result)
    ]


EXPRESS_EXPECTED = [
    ("POST", "/orders", 201, False, None),
    ("GET", "/users/:id", 200, False, None),
    ("GET", "/boom", 500, True, None),
    ("GET", UNMATCHED_ROUTE, 404, False, None),  # Express reports "/" here; see mapping
]


def test_express_json_fixture():
    result = map_resource_spans(json.loads((FIXTURES / "express_http_json.json").read_text()))
    assert sorted(_summary(result)) == sorted(EXPRESS_EXPECTED)
    assert result.rejected == 0
    assert result.skipped == 22  # client + internal (middleware, tcp) spans
    for e in _events(result):
        assert e["service_name"] == "express-demo"
        assert e["release"] == "v1.2.3"
        assert e["environment"] == "prod"
        assert e["host"] == "web-1"
        assert 0 < e["duration_ms"] < 1000


def test_express_protobuf_fixture_maps_like_json():
    result = map_resource_spans(_proto_fixture("express_http_protobuf.bin"))
    assert sorted(_summary(result)) == sorted(EXPRESS_EXPECTED)
    assert result.skipped == 22


def test_python_legacy_semconv_fixture():
    result = map_resource_spans(_proto_fixture("fastapi_python_legacy.bin"))
    assert sorted(_summary(result)) == sorted([
        ("GET", "/items/{item_id}", 200, False, None),
        ("GET", "/items/{item_id}", 404, False, None),
        ("GET", "/crash", 500, True, "RuntimeError"),  # from the exception event on the span
        ("GET", UNMATCHED_ROUTE, 404, False, None),  # no http.route at all
    ])
    e = _events(result)[0]
    assert (e["release"], e["environment"], e["host"]) == ("2026.10.1", "staging", "api-pod-3")


def test_event_ids_are_stable_across_retries():
    payload = json.loads((FIXTURES / "express_http_json.json").read_text())
    first = {r[0] for r in map_resource_spans(payload).rows}
    second = {r[0] for r in map_resource_spans(payload).rows}
    assert first == second and len(first) == 4


# --- synthetic edge cases ----------------------------------------------------

def _payload(span, resource=None):
    resource = resource if resource is not None else {"service.name": "svc"}
    return {"resourceSpans": [{
        "resource": {"attributes": [{"key": k, "value": {"stringValue": v}} for k, v in resource.items()]},
        "scopeSpans": [{"spans": [span]}],
    }]}


def _span(attrs, kind=2, status=None, start="1700000000000000000", end="1700000000050000000"):
    def wrap(v):
        return {"intValue": str(v)} if isinstance(v, int) else {"stringValue": v}
    span = {
        "traceId": "5b8efff798038103d269b633813fc60c", "spanId": "eee19b7ec3c1b174",
        "kind": kind, "startTimeUnixNano": start, "endTimeUnixNano": end,
        "attributes": [{"key": k, "value": wrap(v)} for k, v in attrs.items()],
    }
    if status is not None:
        span["status"] = {"code": status}
    return span


HTTP_OK = {"http.request.method": "GET", "http.response.status_code": 200, "http.route": "/a"}


def test_enum_names_from_message_to_dict_are_accepted():
    result = map_resource_spans(_payload(_span(HTTP_OK, kind="SPAN_KIND_SERVER")))
    assert len(result.rows) == 1


def test_non_http_server_span_is_skipped_not_rejected():
    result = map_resource_spans(_payload(_span({"rpc.system": "grpc"})))
    assert (len(result.rows), result.skipped, result.rejected) == (0, 1, 0)


def test_missing_service_name_uses_otel_default():
    result = map_resource_spans(_payload(_span(HTTP_OK), resource={}))
    assert _events(result)[0]["service_name"] == "unknown_service"


def test_no_status_code_with_error_status_counts_as_500():
    span = _span({"http.request.method": "GET", "http.route": "/a"}, status=2)
    e = _events(map_resource_spans(_payload(span)))[0]
    assert (e["status_code"], e["is_error"]) == (500, True)


def test_no_status_code_without_error_is_rejected():
    span = _span({"http.request.method": "GET", "http.route": "/a"})
    result = map_resource_spans(_payload(span))
    assert result.rejected == 1 and "status_code" in result.reasons[0]


def test_negative_duration_is_rejected():
    span = _span(HTTP_OK, start="1700000000050000000", end="1700000000000000000")
    assert map_resource_spans(_payload(span)).rejected == 1


def test_numeric_error_type_is_not_used_as_exception_name():
    span = _span({**HTTP_OK, "http.response.status_code": 503, "error.type": "503"})
    e = _events(map_resource_spans(_payload(span)))[0]
    assert (e["is_error"], e["error_type"]) == (True, None)


def test_root_route_is_kept_for_the_root_path():
    span = _span({**HTTP_OK, "http.route": "/", "url.path": "/"})
    assert _events(map_resource_spans(_payload(span)))[0]["route"] == "/"


def test_body_sizes_are_mapped():
    span = _span({**HTTP_OK, "http.request.body.size": 12, "http.response.body.size": 345})
    e = _events(map_resource_spans(_payload(span)))[0]
    assert (e["request_bytes"], e["response_bytes"]) == (12, 345)


# --- GenAI spans -> LLM usage on the request -------------------------------------

def _tree(*spans):
    """One export with several spans of the same trace."""
    return {"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "svc"}}]},
        "scopeSpans": [{"spans": list(spans)}],
    }]}


def _child(span_id, parent_id, attrs, kind=3):  # 3 = CLIENT
    span = _span(attrs, kind=kind)
    span["spanId"], span["parentSpanId"] = span_id, parent_id
    return span


SERVER_ID = "eee19b7ec3c1b174"


def test_genai_span_tokens_land_on_the_request_that_made_the_call():
    server = _span({**HTTP_OK, "http.route": "/chat"})
    handler = _child("1111111111111111", SERVER_ID, {"code.function": "chat"}, kind=1)  # INTERNAL
    call1 = _child("2222222222222222", "1111111111111111", {
        "gen_ai.request.model": "gpt-4o-mini", "gen_ai.response.model": "gpt-4o-mini-2024-07-18",
        "gen_ai.usage.input_tokens": 1200, "gen_ai.usage.output_tokens": 240})
    call2 = _child("3333333333333333", SERVER_ID, {
        "gen_ai.request.model": "gpt-4o", "gen_ai.usage.prompt_tokens": 100,  # legacy names
        "gen_ai.usage.completion_tokens": 20})
    [event] = _events(map_resource_spans(_tree(server, handler, call1, call2)))
    assert event["route"] == "/chat"
    # tokens summed, attributed to the model with the most tokens (like the SDKs)
    assert (event["llm_model"], event["llm_input_tokens"], event["llm_output_tokens"]) == \
        ("gpt-4o-mini-2024-07-18", 1300, 260)


def test_genai_span_without_its_request_in_the_export_is_ignored():
    orphan = _child("2222222222222222", "9999999999999999", {
        "gen_ai.request.model": "gpt-4o-mini", "gen_ai.usage.input_tokens": 10})
    server = _span(HTTP_OK)
    [event] = _events(map_resource_spans(_tree(server, orphan)))
    assert event["llm_model"] is None and event["llm_input_tokens"] is None


def test_request_without_llm_calls_has_no_llm_usage():
    [event] = _events(map_resource_spans(_payload(_span(HTTP_OK))))
    assert event["llm_model"] is None


def test_genai_span_needs_a_model_and_sane_token_counts():
    server = _span(HTTP_OK)
    no_model = _child("2222222222222222", SERVER_ID, {"gen_ai.usage.input_tokens": 10})
    huge = _child("3333333333333333", SERVER_ID, {"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 10**12})
    [event] = _events(map_resource_spans(_tree(server, no_model, huge)))
    assert event["llm_model"] is None
