"""The collector's own /metrics and JSON logs. No database needed."""

import dataclasses
import json
import logging
import sys

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app import observability
from app.logs import JsonFormatter
from app.main import app


def _requests(route, status):
    return REGISTRY.get_sample_value(
        "reqly_http_requests_total", {"method": "GET", "route": route, "status": status}
    ) or 0


def test_requests_are_counted_by_route_template_not_path():
    client = TestClient(app)
    before = _requests("/v1/health", "200"), _requests(observability.UNMATCHED_ROUTE, "404")
    client.get("/v1/health")
    client.get("/wp-admin/setup.php")  # a scanner's path must not become a label
    assert _requests("/v1/health", "200") == before[0] + 1
    assert _requests(observability.UNMATCHED_ROUTE, "404") == before[1] + 1

    body = client.get("/metrics").text
    assert 'reqly_http_requests_total{method="GET",route="/v1/health",status="200"}' in body
    assert "reqly_http_request_duration_seconds_bucket" in body
    assert "reqly_late_data_pending_seconds" in body
    assert "wp-admin" not in body


def test_metrics_token_and_switch(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(observability, "settings", dataclasses.replace(observability.settings, metrics_token="s3cret"))
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    monkeypatch.setattr(observability, "settings", dataclasses.replace(observability.settings, metrics_enabled=False))
    assert client.get("/metrics").status_code == 404


def test_json_log_lines():
    formatter = JsonFormatter()
    record = logging.LogRecord("reqly.collector", logging.WARNING, __file__, 1, "pool %s", ("ready",), None)
    line = json.loads(formatter.format(record))
    assert (line["level"], line["logger"], line["message"]) == ("warning", "reqly.collector", "pool ready")
    assert line["time"].endswith("+00:00")

    access = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5000", "POST", "/v1/ingest", "1.1", 200), None,
    )
    line = json.loads(formatter.format(access))
    assert (line["method"], line["path"], line["status"]) == ("POST", "/v1/ingest", 200)

    try:
        raise ValueError("boom")
    except ValueError:
        failed = logging.LogRecord("reqly.collector", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    assert "ValueError: boom" in json.loads(formatter.format(failed))["exception"]
