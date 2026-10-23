"""Reverse proxy access logs: formats, route templates, and the endpoint
end to end (the last part needs TimescaleDB)."""

import gzip
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import pool as pool_module
from app.main import app
from app.openapi.drift import spec_operations
from app.proxylogs import LineError, parse_body, parse_line, route_template
from tests.test_queries_db import _db_reachable

NOW = datetime.now(timezone.utc).replace(microsecond=0)
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def caddy(uri="/users/42?tab=1", status=200, duration=0.0123, ts=None):
    return {"level": "info", "ts": (ts or NOW - timedelta(seconds=30)).timestamp(), "logger": "http.log.access",
            "msg": "handled request", "duration": duration, "size": 1024, "status": status,
            "request": {"remote_ip": "203.0.113.9", "proto": "HTTP/2.0", "method": "GET", "host": "api.example.com",
                        "uri": uri, "headers": {"User-Agent": ["curl/8"], "Traceparent": [f"00-{TRACE}-00f067aa0ba902b7-01"]}}}


def traefik(path="/orders", status=503, duration_ns=45_000_000):
    return {"RequestMethod": "POST", "RequestPath": path, "DownstreamStatus": status, "Duration": duration_ns,
            "StartUTC": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"), "RequestHost": "api",
            "RequestContentSize": 120, "DownstreamContentSize": 12}


def nginx(uri="/orders/9f8e7d6c-1a2b-3c4d-5e6f-001122334455/items", status=404):
    return {"time_iso8601": (NOW - timedelta(minutes=2)).isoformat(), "method": "DELETE", "request_uri": uri,
            "status": str(status), "request_time": "0.250", "body_bytes_sent": "0", "request_length": "312",
            "host": "api.example.com"}


def test_each_format_parses():
    c = parse_line(caddy(), NOW)
    assert (c.method, c.path, c.status, c.host, c.response_bytes) == ("GET", "/users/42", 200, "api.example.com", 1024)
    assert c.duration_ms == pytest.approx(12.3) and c.traceparent.startswith("00-" + TRACE)
    t = parse_line(traefik(), NOW)
    assert (t.method, t.path, t.status, t.duration_ms, t.request_bytes) == ("POST", "/orders", 503, 45.0, 120)
    assert t.time == NOW - timedelta(minutes=1) + timedelta(milliseconds=45)
    n = parse_line(nginx(), NOW)
    assert (n.method, n.status, n.duration_ms, n.request_bytes) == ("DELETE", 404, 250.0, 312)
    envoy = parse_line({"method": "GET", "path": "/x", "response_code": 200, "duration": 7,
                        "start_time": NOW.isoformat()}, NOW)
    assert envoy.duration_ms == 7


def test_lines_without_what_reqly_needs_are_rejected():
    for bad, reason in ((nginx() | {"request_time": None}, "no duration"), ({"method": "GET", "status": 200}, "no method or path"),
                        (nginx() | {"status": "999"}, "status")):
        with pytest.raises(LineError, match=reason):
            parse_line(bad, NOW)


def test_bodies_as_ndjson_or_an_array():
    assert len(parse_body(b'{"a": 1}\n\n{"b": 2}\nnot json\n')) == 3
    assert parse_body(b'[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]
    assert parse_body(b"") == []


def test_route_templates_never_keep_ids():
    assert route_template("GET", "/users/42", []) == "/users/{id}"
    assert route_template("GET", "/orders/9f8e7d6c-1a2b-3c4d-5e6f-001122334455/items", []) == "/orders/{id}/items"
    assert route_template("GET", "/files/a3f9c2e1b7d04e6f8a12", []) == "/files/{id}"
    assert route_template("GET", "/blog/hello-world", []) == "/blog/hello-world"  # a slug stays
    assert route_template("GET", "/", []) == "/"
    spec = {"openapi": "3.0.0", "paths": {"/users/{user_id}": {"get": {}}, "/users/me": {"get": {}},
                                          "/teams/{slug}/members": {"post": {}}}}
    ops = spec_operations(spec)
    assert route_template("GET", "/users/42", ops) == "/users/{user_id}"
    assert route_template("GET", "/users/me", ops) == "/users/me"
    assert route_template("GET", "/teams/acme/members", ops) == "/teams/{slug}/members"  # any method's path
    assert route_template("GET", "/health/42", ops) == "/health/{id}"  # not in the spec: the heuristic


@pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")
def test_logs_endpoint_end_to_end():
    service = f"test-proxy-{uuid.uuid4().hex[:8]}"
    headers = {"X-Reqly-Key": settings.ingest_key, "Content-Type": "application/x-ndjson", "Content-Encoding": "gzip"}
    lines = [caddy(), caddy(uri="/users/7", status=500), traefik(), nginx(), {"method": "GET"}, "oops"]
    body = gzip.compress("\n".join(json.dumps(x) for x in lines).encode())
    with TestClient(app) as client:
        pool = pool_module.get_pool()
        call = client.portal.call
        try:
            url = f"/v1/ingest/logs?service_name={service}&release=v9&environment=production"
            r = client.post(url, headers=headers, content=body)
            assert r.status_code == 200, r.text
            assert (r.json()["accepted"], r.json()["rejected"]) == (4, 2)
            again = client.post(url, headers=headers, content=body)  # a shipper re-sending: deduplicated
            assert again.json()["accepted"] == 4
            rows = call(pool.fetch, "SELECT method, route, status_code, is_error, release, environment, trace_id "
                                    "FROM request_events WHERE service_name = $1 ORDER BY route, status_code", service)
            assert [(r["method"], r["route"], r["status_code"]) for r in rows] == [
                ("POST", "/orders", 503), ("DELETE", "/orders/{id}/items", 404),
                ("GET", "/users/{id}", 200), ("GET", "/users/{id}", 500)]
            assert all(r["release"] == "v9" and r["environment"] == "production" for r in rows)
            # trace ids kept for the failures only, like the SDKs
            assert [r["trace_id"] for r in rows if r["route"] == "/users/{id}"] == [None, TRACE]
            assert client.post(f"/v1/ingest/logs?service_name={service}", content=b"{}",
                               headers={"X-Reqly-Key": settings.read_key}).status_code == 401
        finally:
            call(pool.execute, "DELETE FROM request_events WHERE service_name = $1", service)
            call(pool.execute, "DELETE FROM deployments WHERE service_name = $1", service)
            call(pool.execute, "DELETE FROM project_services WHERE service_name = $1", service)
