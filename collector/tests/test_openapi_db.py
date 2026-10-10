"""OpenAPI drift against TimescaleDB (skipped when unreachable)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.ask import tools
from app.db import pool as pool_module
from app.db import queries
from app.openapi import store
from tests.test_queries_db import _db_reachable, _refresh_all, aggregate_mode  # noqa: F401

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Shop", "version": "1.4"},
    "paths": {
        "/users/{user_id}": {"get": {}, "delete": {}},
        "/products": {"get": {"deprecated": True}},
    },
}


def test_drift_report_from_stored_spec_and_traffic(aggregate_mode):  # noqa: F811
    service = f"test-drift-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    traffic = [("GET", "/users/<int:id>", 40), ("GET", "/products", 10), ("POST", "/internal/sync", 5)]
    rows = []
    for method, route, n in traffic:
        for i in range(n):
            rows.append(queries.event_row(
                event_id=str(uuid.uuid4()), time=now - timedelta(days=3, minutes=i), service_name=service,
                method=method, route=route, status_code=500 if i == 0 else 200, duration_ms=20.0,
                is_error=i == 0, error_type=None, host="h",
            ))

    async def run():
        pool = await pool_module.create_pool()
        try:
            await queries.insert_events(pool, rows)
            await _refresh_all(pool)
            missing = await store.drift_report(pool, service, now)
            no_spec_tool = await tools.run_tool(pool, service, "get_api_drift", {}, now)
            await store.save_spec(pool, service, SPEC, "")
            await store.save_spec(pool, service, SPEC, "")  # re-upload replaces
            report = await store.drift_report(pool, service, now)
            tool = await tools.run_tool(pool, service, "get_api_drift", {}, now)
            return missing, no_spec_tool, report, tool
        finally:
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool.execute("DELETE FROM api_specs WHERE service_name = $1", service)
            await pool_module.close_pool()

    missing, no_spec_tool, report, tool = asyncio.run(run())
    assert missing is None
    assert no_spec_tool["spec"] is None
    assert report["spec"]["title"] == "Shop" and report["spec"]["version"] == "1.4"
    assert report["window_days"] == (30 if aggregate_mode == "sketches" else 14)
    assert [(u["method"], u["route"], u["requests"]) for u in report["undocumented"]] == [
        ("POST", "/internal/sync", 5)
    ]
    assert report["undocumented"][0]["error_rate"] == 0.2
    assert [(d["method"], d["path"]) for d in report["dead"]] == [("DELETE", "/users/{user_id}")]
    assert [(d["path"], d["requests"]) for d in report["deprecated_in_use"]] == [("/products", 10)]
    assert report["total_requests"] == 55 and report["documented_in_use"] == 2

    # the Ask Reqly tool: the same findings, trimmed and rounded
    assert tool["operations_in_spec"] == 3 and tool["operations_with_traffic"] == 2
    assert tool["undocumented"] == [{"method": "POST", "route": "/internal/sync", "requests": 5,
                                     "error_rate": 0.2, "last_seen": tool["undocumented"][0]["last_seen"]}]
    assert tool["undocumented_share_of_requests"] == pytest.approx(5 / 55, rel=1e-3)
    assert tool["dead_count"] == 1
