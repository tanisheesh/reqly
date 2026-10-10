import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

from app.main import app
from app.routers import otlp as otlp_module

FIXTURES = Path(__file__).parent / "fixtures" / "otlp"
KEY = {"X-Reqly-Key": "demo-key"}
RECORDED_AT = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch):
    captured = []

    async def fake_insert(pool, rows):
        captured.extend(rows)

    async def fake_deployments(pool, rows):
        pass

    monkeypatch.setattr(otlp_module, "get_pool", lambda: object())
    monkeypatch.setattr(otlp_module, "insert_events", fake_insert)
    monkeypatch.setattr(otlp_module, "record_deployments", fake_deployments)
    # The fixtures are real exports with fixed timestamps; judge their age as
    # of when they were recorded, so the tests don't expire.
    monkeypatch.setattr(otlp_module, "_now", lambda: RECORDED_AT)
    c = TestClient(app)
    c.captured = captured
    return c


def _post(client, body, content_type, headers=None):
    return client.post(
        "/otlp/v1/traces", content=body,
        headers={"Content-Type": content_type, **KEY, **(headers or {})},
    )


def test_requires_ingest_key(client):
    r = client.post("/otlp/v1/traces", content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 401


def test_rejects_unknown_content_type(client):
    assert _post(client, b"x", "text/plain").status_code == 415


def test_protobuf_export(client):
    body = (FIXTURES / "express_http_protobuf.bin").read_bytes()
    r = _post(client, body, "application/x-protobuf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-protobuf"
    response = ExportTraceServiceResponse()
    response.ParseFromString(r.content)
    assert response.partial_success.rejected_spans == 0
    assert len(client.captured) == 4


def test_gzipped_json_export(client):
    body = gzip.compress((FIXTURES / "express_http_json.json").read_bytes())
    r = _post(client, body, "application/json", {"Content-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.json() == {}
    assert len(client.captured) == 4


def test_partial_success_reports_rejected_spans(client):
    payload = {"resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": [{
        "traceId": "5b8efff798038103d269b633813fc60c", "spanId": "eee19b7ec3c1b174", "kind": 2,
        "startTimeUnixNano": "1", "endTimeUnixNano": "2",
        "attributes": [{"key": "http.request.method", "value": {"stringValue": "GET"}}],
    }]}]}]}
    r = _post(client, json.dumps(payload).encode(), "application/json")
    assert r.status_code == 200
    assert r.json()["partialSuccess"]["rejectedSpans"] == "1"
    assert client.captured == []


def test_invalid_bodies_are_400(client):
    assert _post(client, b"not json", "application/json").status_code == 400
    assert _post(client, b"\xff\xff\xff", "application/x-protobuf").status_code == 400
    assert _post(client, b"not gzip", "application/json", {"Content-Encoding": "gzip"}).status_code == 400


def test_gzip_bomb_is_refused(client):
    bomb = gzip.compress(b"{" + b" " * (40 * 1024 * 1024) + b"}")
    r = _post(client, bomb, "application/json", {"Content-Encoding": "gzip"})
    assert r.status_code == 413


def _span_at(ts: datetime, span_id: str) -> dict:
    nanos = str(int(ts.timestamp() * 1e9))
    return {
        "traceId": "0" * 31 + "1", "spanId": span_id, "name": "GET /a", "kind": 2,
        "startTimeUnixNano": nanos, "endTimeUnixNano": nanos,
        "attributes": [
            {"key": "http.request.method", "value": {"stringValue": "GET"}},
            {"key": "http.route", "value": {"stringValue": "/a"}},
            {"key": "http.response.status_code", "value": {"intValue": "200"}},
        ],
    }


def test_spans_too_old_or_in_the_future_are_rejected(client):
    payload = {"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "svc"}}]},
        "scopeSpans": [{"spans": [
            _span_at(RECORDED_AT - timedelta(minutes=5), "a" * 16),
            _span_at(RECORDED_AT - timedelta(days=20), "b" * 16),
            _span_at(RECORDED_AT + timedelta(hours=1), "c" * 16),
        ]}],
    }]}
    r = _post(client, json.dumps(payload).encode(), "application/json")
    assert r.status_code == 200
    partial = r.json()["partialSuccess"]
    assert partial["rejectedSpans"] == "2"
    assert "older than 13 days" in partial["errorMessage"] and "in the future" in partial["errorMessage"]
    assert len(client.captured) == 1
