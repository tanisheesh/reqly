"""Consumer analytics and LLM cost against TimescaleDB (skipped when unreachable)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.ask import tools
from app.consumers import queries as consumers
from app.db import pool as pool_module
from app.db import queries
from app.llm.usage import llm_usage
from app.openapi import store
from tests.test_queries_db import _db_reachable, _refresh_all

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
INCIDENT = NOW - timedelta(hours=3)  # one hour in which /orders fails for "acme"


def _rows(service):
    """Three days, one request per 10 minutes per stream:
    acme   POST /orders  (fails during INCIDENT) + GET /products (deprecated)
    globex POST /orders
    (none) GET /products, POST /chat with LLM usage (gpt-4o-mini, 1000 in / 200 out)
    """
    rows = []
    t = NOW - timedelta(days=3)
    while t < NOW:
        in_incident = INCIDENT <= t < INCIDENT + timedelta(hours=1)
        for consumer, method, route, extra in (
            ("acme", "POST", "/orders", {}),
            ("acme", "GET", "/products", {}),
            ("globex", "POST", "/orders", {}),
            (None, "GET", "/products", {}),
            (None, "POST", "/chat", {"llm_model": "gpt-4o-mini-2024-07-18", "llm_input_tokens": 1000,
                                     "llm_output_tokens": 200}),
        ):
            bad = in_incident and consumer == "acme" and route == "/orders"
            rows.append(queries.event_row(
                event_id=str(uuid.uuid4()), time=t, service_name=service, method=method, route=route,
                status_code=500 if bad else 200, duration_ms=50.0, is_error=bad, error_type=None,
                host="h", consumer_id=consumer, **extra,
            ))
        t += timedelta(minutes=10)
    return rows


STREAM = 3 * 24 * 6  # requests per stream


def test_consumers_llm_and_drift():
    service = f"test-cons-{uuid.uuid4().hex[:8]}"
    spec = {"openapi": "3.0.0", "paths": {"/orders": {"post": {}}, "/products": {"get": {"deprecated": True}},
                                          "/chat": {"post": {}}}}

    async def run():
        pool = await pool_module.create_pool()
        try:
            await queries.insert_events(pool, _rows(service))
            await _refresh_all(pool)
            await store.save_spec(pool, service, spec, "")
            out = {
                "raw": await consumers.top_consumers(pool, service, "7d", NOW),
                "rollup": await consumers.top_consumers(pool, service, "30d", NOW),
                "detail": await consumers.consumer_detail(pool, service, "acme", "7d", NOW),
                "detail_rollup": await consumers.consumer_detail(pool, service, "acme", "30d", NOW),
                "nobody": await consumers.consumer_detail(pool, service, "initech", "7d", NOW),
                "llm_raw": await llm_usage(pool, service, "7d", NOW),
                "llm_rollup": await llm_usage(pool, service, "30d", NOW),
                "drift": await store.drift_report(pool, service, NOW),
                "tool_consumers": await tools.run_tool(pool, service, "get_consumers", {}, NOW),
                "tool_llm": await tools.run_tool(pool, service, "get_llm_costs", {"window": "24h"}, NOW),
                "tool_breakdown": await tools.run_tool(pool, service, "get_breakdown", {
                    "dimension": "consumer_id", "route": "/orders",
                    "start": INCIDENT.isoformat(), "end": (INCIDENT + timedelta(hours=1)).isoformat()}, NOW),
            }
            async with pool.acquire() as conn:
                out["affected"] = await consumers.affected_consumers(
                    conn, service, "/orders", INCIDENT, INCIDENT + timedelta(hours=1))
                out["quiet_hour"] = await consumers.affected_consumers(
                    conn, service, "/chat", INCIDENT, INCIDENT + timedelta(hours=1))
            anomalies = [{"route": "/orders", "window_start": INCIDENT.isoformat()}]
            await consumers.add_affected_consumers(pool, service, anomalies)
            out["anomalies"] = anomalies
            return out
        finally:
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool.execute("DELETE FROM api_specs WHERE service_name = $1", service)
            await pool_module.close_pool()

    out = asyncio.run(run())

    # top consumers: same counts from raw events and from the hourly rollup
    for key in ("raw", "rollup"):
        top = out[key]
        assert top["requests"] == 5 * STREAM and top["requests_with_consumer"] == 3 * STREAM
        assert top["consumers"] == 2
        assert [(c["consumer_id"], c["requests"], c["errors"], c["routes"]) for c in top["top"]] == [
            ("acme", 2 * STREAM, 6, 2), ("globex", STREAM, 0, 1)]
        assert top["top"][0]["share_of_requests"] == pytest.approx(0.4)
    assert out["raw"]["top"][0]["p95_ms"] == 50 and out["rollup"]["top"][0]["p95_ms"] is None

    for key in ("detail", "detail_rollup"):
        # both routes have the same request count, so their order is arbitrary
        assert sorted((r["method"], r["route"], r["errors"]) for r in out[key]["routes"]) == [
            ("GET", "/products", 0), ("POST", "/orders", 6)]
        assert sum(d["requests"] for d in out[key]["daily"]) == 2 * STREAM
    assert out["nobody"] is None

    assert out["affected"] == {"active": 2, "affected": 1,
                               "top": [{"consumer_id": "acme", "requests": 6, "errors": 6}]}
    assert out["quiet_hour"] is None  # /chat has no consumer ids
    assert out["anomalies"][0]["affected_consumers"]["affected"] == 1

    # LLM cost: gpt-4o-mini at $0.15 / $0.60 per 1M tokens
    per_request = (1000 * 0.15 + 200 * 0.60) / 1_000_000
    for key in ("llm_raw", "llm_rollup"):
        usage = out[key]
        assert [r["route"] for r in usage["routes"]] == ["/chat"]
        chat = usage["routes"][0]
        assert chat["llm_requests"] == STREAM and chat["requests"] == STREAM
        assert chat["models"][0]["priced_as"] == "gpt-4o-mini"
        assert chat["cost_usd"] == pytest.approx(STREAM * per_request, rel=1e-6)
        assert usage["totals"]["requests"] == 5 * STREAM
        assert sum(d["cost_usd"] for d in usage["daily"]) == pytest.approx(chat["cost_usd"], rel=1e-6)
        assert usage["unpriced_models"] == []

    # drift: the deprecated GET /products is still called -- by acme
    [deprecated] = out["drift"]["deprecated_in_use"]
    assert deprecated["path"] == "/products"
    assert [c["consumer_id"] for c in deprecated["consumers"]] == ["acme"]

    # Ask Reqly tools
    assert [c["consumer_id"] for c in out["tool_consumers"]["top"]] == ["acme", "globex"]
    assert out["tool_consumers"]["share_of_requests_with_consumer"] == pytest.approx(0.6)
    assert out["tool_llm"]["routes"][0]["route"] == "/chat" and out["tool_llm"]["total_cost_usd"] > 0
    breakdown = {v["value"]: v for v in out["tool_breakdown"]["values"]}
    assert breakdown["acme"]["share_of_errors"] == 1 and breakdown["globex"]["errors"] == 0
