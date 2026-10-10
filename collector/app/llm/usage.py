"""LLM token usage and estimated cost per route."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import asyncpg

from .prices import PriceTable, price_table

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
RAW_WINDOWS = ("24h", "7d")


async def _rows(pool, service: str, window: str, start: datetime):
    """(route, model, requests, input, output) plus per-day cost rows."""
    if window in RAW_WINDOWS:
        rows = await pool.fetch(
            """
            SELECT route, llm_model, count(*) AS requests,
                   coalesce(sum(llm_input_tokens), 0) AS input_tokens,
                   coalesce(sum(llm_output_tokens), 0) AS output_tokens
            FROM request_events WHERE service_name = $1 AND time > $2
            GROUP BY route, llm_model
            """,
            service, start,
        )
        daily = await pool.fetch(
            """
            SELECT time_bucket('1 day', time) AS day, llm_model,
                   sum(llm_input_tokens) AS input_tokens, sum(llm_output_tokens) AS output_tokens
            FROM request_events
            WHERE service_name = $1 AND time > $2 AND llm_model IS NOT NULL
            GROUP BY 1, 2 ORDER BY 1
            """,
            service, start,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT route, llm_model, sum(request_count) AS requests,
                   sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens
            FROM llm_usage_1hour WHERE service_name = $1 AND bucket > $2
            GROUP BY route, llm_model
            """,
            service, start,
        )
        daily = await pool.fetch(
            """
            SELECT time_bucket('1 day', bucket) AS day, llm_model,
                   sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens
            FROM llm_usage_1hour
            WHERE service_name = $1 AND bucket > $2 AND llm_model IS NOT NULL
            GROUP BY 1, 2 ORDER BY 1
            """,
            service, start,
        )
    return rows, daily


def summarize(rows, daily, prices: PriceTable) -> dict:
    """Pure: per-route token and cost totals from (route, model) rows."""
    routes: dict[str, dict] = {}
    unpriced: dict[str, int] = defaultdict(int)
    totals = {"requests": 0, "llm_requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    for r in rows:
        route = routes.setdefault(r["route"], {
            "route": r["route"], "requests": 0, "llm_requests": 0, "input_tokens": 0,
            "output_tokens": 0, "cost_usd": 0.0, "models": {},
        })
        requests = int(r["requests"])
        route["requests"] += requests
        totals["requests"] += requests
        model = r["llm_model"]
        if model is None:
            continue
        tokens_in, tokens_out = int(r["input_tokens"]), int(r["output_tokens"])
        match = prices.lookup(model)
        cost = match[1].cost(tokens_in, tokens_out) if match else None
        if match is None:
            unpriced[model] += tokens_in + tokens_out
        for target in (route, totals):
            target["llm_requests"] += requests
            target["input_tokens"] += tokens_in
            target["output_tokens"] += tokens_out
            target["cost_usd"] += cost or 0.0
        route["models"][model] = {
            "model": model,
            "priced_as": match[0] if match else None,
            "llm_requests": requests,
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "cost_usd": cost,
        }

    out_routes = []
    for route in routes.values():
        if not route["llm_requests"]:
            continue
        route["models"] = sorted(route["models"].values(), key=lambda m: -(m["cost_usd"] or 0))
        route["cost_usd"] = round(route["cost_usd"], 6)
        route["cost_per_1k_requests"] = round(1000 * route["cost_usd"] / route["requests"], 6)
        route["tokens_per_llm_request"] = round(
            (route["input_tokens"] + route["output_tokens"]) / route["llm_requests"], 1
        )
        out_routes.append(route)
    out_routes.sort(key=lambda r: (-r["cost_usd"], -r["input_tokens"]))

    per_day: dict = defaultdict(float)
    for d in daily:
        match = prices.lookup(d["llm_model"])
        per_day[d["day"]] += match[1].cost(int(d["input_tokens"] or 0), int(d["output_tokens"] or 0)) if match else 0.0

    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return {
        "totals": totals,
        "routes": out_routes,
        "daily": [{"day": day, "cost_usd": round(cost, 6)} for day, cost in sorted(per_day.items())],
        "unpriced_models": [{"model": m, "tokens": t} for m, t in sorted(unpriced.items(), key=lambda x: -x[1])],
        "prices_as_of": prices.as_of,
    }


async def llm_usage(pool: asyncpg.Pool, service: str, window: str, now: datetime | None = None) -> dict:
    if window not in WINDOWS:
        raise ValueError(f"unsupported window: {window!r}")
    now = now or datetime.now(timezone.utc)
    rows, daily = await _rows(pool, service, window, now - WINDOWS[window])
    return {"service_name": service, "window": window, **summarize(rows, daily, price_table())}
