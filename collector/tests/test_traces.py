"""Trace ids: which are kept, from ingest and OTLP, and the ones an alert
points at (the last part needs TimescaleDB)."""

import asyncio
import dataclasses
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import traces
from app.db import pool as pool_module
from app.db import queries
from app.db.queries import EVENT_COLUMNS
from app.otlp.mapping import map_resource_spans
from tests.test_ingest import _valid_event, client  # noqa: F401 -- the ingest fixture
from tests.test_queries_db import _db_reachable

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def test_trace_id_shapes():
    assert traces.normalize_trace_id(TRACE.upper()) == TRACE
    assert traces.normalize_trace_id("463ac35c9f6413ad") == "463ac35c9f6413ad"  # B3 64-bit
    for bad in ("0" * 32, "xyz", TRACE + "00", None, 42):
        assert traces.normalize_trace_id(bad) is None


def test_only_failed_and_slow_requests_keep_it():
    assert traces.keep_trace_id(TRACE, 500, 5) == TRACE
    assert traces.keep_trace_id(TRACE, 401, 5) == TRACE
    assert traces.keep_trace_id(TRACE, 200, 2500) == TRACE
    assert traces.keep_trace_id(TRACE, 200, 5) is None
    assert traces.keep_trace_id(None, 500, 5) is None


def test_ingest_keeps_trace_ids_by_the_rule(client):  # noqa: F811
    events = [
        _valid_event(status_code=503, error=True, trace_id=TRACE),
        _valid_event(trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),  # fast 200: dropped
        _valid_event(status_code=500, error=True, trace_id="not-a-trace"),  # malformed: the event stays
    ]
    response = client.post("/v1/ingest", headers={"X-Reqly-Key": "demo-key"}, json={"service_name": "s", "events": events})
    assert response.json() == {"accepted": 3, "rejected": 0}
    assert [dict(zip(EVENT_COLUMNS, r))["trace_id"] for r in client.captured_rows] == [TRACE, None, None]


def test_otlp_error_spans_keep_their_trace_id():
    nanos = int(datetime.now(timezone.utc).timestamp() * 1e9)

    def span(status):
        return {"traceId": TRACE, "spanId": uuid.uuid4().hex[:16], "kind": 2,
                "startTimeUnixNano": str(nanos), "endTimeUnixNano": str(nanos + 1_000_000),
                "attributes": [{"key": "http.request.method", "value": {"stringValue": "GET"}},
                               {"key": "http.response.status_code", "value": {"intValue": str(status)}}]}

    payload = {"resourceSpans": [{"scopeSpans": [{"spans": [span(500), span(200)]}]}]}
    rows = [dict(zip(EVENT_COLUMNS, r)) for r in map_resource_spans(payload).rows]
    assert sorted((r["status_code"], r["trace_id"]) for r in rows) == [(200, None), (500, TRACE)]


@pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")
def test_alerts_point_at_the_hours_failed_traces_first(monkeypatch):
    service = f"test-trace-{uuid.uuid4().hex[:8]}"
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    monkeypatch.setattr(traces, "settings", dataclasses.replace(
        traces.settings, trace_url_template="https://tempo.example/trace/{trace_id}"))

    def event(trace_id, status, duration):
        return queries.event_row(
            event_id=str(uuid.uuid4()), time=hour + timedelta(minutes=5), service_name=service, method="GET",
            route="/orders", status_code=status, duration_ms=duration, is_error=status >= 500, error_type=None,
            host="h", trace_id=trace_id,
        )

    async def run():
        pool = await pool_module.create_pool()
        try:
            await queries.insert_events(pool, [
                event("a" * 32, 200, 4000), event("b" * 32, 500, 20), event("c" * 32, 401, 10), event(None, 500, 1),
            ])
            anomaly = [{"route": "/orders", "window_start": hour.isoformat()}]
            spike = [{"kind": "client_errors", "route": "/orders", "status_code": 401, "window_start": hour.isoformat()}]
            await traces.add_trace_ids(pool, service, anomaly)
            await traces.add_trace_ids(pool, service, spike)
            return anomaly[0], spike[0]
        finally:
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool_module.close_pool()

    anomaly, spike = asyncio.run(run())
    assert anomaly["trace_ids"] == ["b" * 32, "a" * 32, "c" * 32]  # the error, then the slowest
    assert anomaly["trace_urls"][0] == "https://tempo.example/trace/" + "b" * 32
    assert spike["trace_ids"] == ["c" * 32]
    json.dumps(anomaly)
