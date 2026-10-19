"""Multi-model requests are priced per model, from raw events and from the
hourly rollups, in the usage report and the cost alerts' rows (skipped when
TimescaleDB is unreachable)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.alerts import llm_cost
from app.db import pool as pool_module
from app.db import queries
from app.llm.prices import price_table
from app.llm.splits import split_rows, summarize_models
from app.llm.usage import llm_usage
from tests.test_queries_db import _db_reachable, _refresh_all

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
HOUR = NOW - timedelta(hours=2)
AGENT_CALLS = [("gpt-4o", 1000, 300), ("gpt-4o-mini", 400, 40)]


def _events(service):
    events, splits = [], []
    for i in range(6):  # /agent calls both models; /chat one
        t = HOUR + timedelta(minutes=5 * i)
        model, tokens_in, tokens_out, per_model = summarize_models(AGENT_CALLS)
        event_id = str(uuid.uuid4())
        events.append(queries.event_row(
            event_id=event_id, time=t, service_name=service, method="POST", route="/agent", status_code=200,
            duration_ms=900.0, is_error=False, error_type=None, host="h",
            llm_model=model, llm_input_tokens=tokens_in, llm_output_tokens=tokens_out,
        ))
        splits += split_rows(event_id, t, service, "/agent", per_model)
        events.append(queries.event_row(
            event_id=str(uuid.uuid4()), time=t, service_name=service, method="POST", route="/chat", status_code=200,
            duration_ms=300.0, is_error=False, error_type=None, host="h",
            llm_model="gpt-4o-mini", llm_input_tokens=100, llm_output_tokens=10,
        ))
    return events, splits


def test_each_model_of_a_request_is_priced_at_its_own_rate():
    service = f"test-split-{uuid.uuid4().hex[:8]}"

    async def run():
        pool = await pool_module.create_pool()
        try:
            events, splits = _events(service)
            await queries.insert_events(pool, events)
            await queries.insert_llm_splits(pool, splits)
            await queries.insert_llm_splits(pool, splits)  # a retried batch adds nothing
            await _refresh_all(pool)
            return (
                await llm_usage(pool, service, "24h", NOW),
                await llm_usage(pool, service, "30d", NOW),
                await llm_cost.hour_rows(pool, service, HOUR),
            )
        finally:
            for table in ("request_events", "llm_usage_splits"):
                await pool.execute(f"DELETE FROM {table} WHERE service_name = $1", service)
            await pool_module.close_pool()

    raw, rollup, alert_rows = asyncio.run(run())
    prices = price_table()
    agent_cost = 6 * sum(prices.lookup(m)[1].cost(i, o) for m, i, o in AGENT_CALLS)
    chat_cost = 6 * prices.lookup("gpt-4o-mini")[1].cost(100, 10)
    as_one_model = 6 * prices.lookup("gpt-4o")[1].cost(1400, 340)
    assert agent_cost < as_one_model  # what the split fixes: mini's tokens at gpt-4o's price

    for report in (raw, rollup):
        routes = {r["route"]: r for r in report["routes"]}
        agent = routes["/agent"]
        assert (agent["requests"], agent["llm_requests"]) == (6, 6)  # splits count no requests
        assert (agent["input_tokens"], agent["output_tokens"]) == (6 * 1400, 6 * 340)
        models = {m["model"]: m for m in agent["models"]}
        assert (models["gpt-4o"]["input_tokens"], models["gpt-4o"]["output_tokens"]) == (6000, 1800)
        assert (models["gpt-4o-mini"]["input_tokens"], models["gpt-4o-mini"]["output_tokens"]) == (2400, 240)
        assert agent["cost_usd"] == pytest.approx(agent_cost, abs=1e-6)
        assert report["totals"]["cost_usd"] == pytest.approx(agent_cost + chat_cost, abs=1e-6)
        assert sum(d["cost_usd"] for d in report["daily"]) == pytest.approx(agent_cost + chat_cost, abs=1e-6)

    tokens: dict = {}
    for r in alert_rows:
        if r["route"] == "/agent" and r["llm_model"]:
            tokens[r["llm_model"]] = tokens.get(r["llm_model"], 0) + int(r["input_tokens"])
    assert tokens == {"gpt-4o": 6000, "gpt-4o-mini": 2400}
