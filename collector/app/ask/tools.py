"""Read-only tools the Ask Reqly model can call.

Every tool is a fixed, parameterized query over one service: the model
picks the tool and its arguments (validated here), never SQL. Results are
small and numeric -- aggregates, never raw events -- so answers can be
traced back to the numbers they quote.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import asyncpg

from ..consumers import queries as consumer_queries
from ..db import queries
from ..llm.usage import llm_usage
from ..openapi import store as openapi_store
from ..slo import status as slo_status

RAW_RETENTION = timedelta(days=14)
AGGREGATE_RETENTION = timedelta(days=90)
DEFAULT_RANGE = timedelta(hours=24)
MAX_SERIES_POINTS = 72
MAX_GROUPS = 25

BREAKDOWN_DIMENSIONS = ("host", "environment", "release", "status_code", "error_type", "method", "consumer_id")
GROUP_BY = ("none", "route", "hour", "day")


class ToolError(Exception):
    """Bad arguments; the message goes back to the model so it can retry."""


def _parse_time(value, name: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ToolError(f"{name} must be an ISO 8601 timestamp, got {value!r}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def time_range(args: dict, now: datetime, retention: timedelta, prefix: str = "") -> tuple[datetime, datetime]:
    """(start, end) from the arguments, defaulting to the last 24h and
    clamped to what the data source still holds."""
    end = _parse_time(args.get(f"{prefix}end"), f"{prefix}end") or now
    start = _parse_time(args.get(f"{prefix}start"), f"{prefix}start") or end - DEFAULT_RANGE
    end = min(end, now)
    start = max(start, now - retention)
    if start >= end:
        raise ToolError(
            f"empty time range {start.isoformat()} .. {end.isoformat()} "
            f"(data goes back {retention.days} days from now)"
        )
    return start, end


def _num(value, digits: int = 4):
    """Rounded for the model: fewer tokens, and no false precision."""
    if value is None:
        return None
    value = float(value)
    if math.isnan(value):
        return None
    if value == int(value) and abs(value) < 1e15:
        return int(value)
    return float(f"{value:.{digits}g}")


def _ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _stats_row(row) -> dict:
    requests = int(row["requests"] or 0)
    errors = int(row["errors"] or 0)
    return {
        "requests": requests,
        "errors": errors,
        "error_rate": _num(errors / requests) if requests else None,
        "p50_ms": _num(row["p50_ms"]),
        "p95_ms": _num(row["p95_ms"]),
        "p99_ms": _num(row["p99_ms"]),
    }


async def _stats(pool, service, start, end, route, environment, group_by) -> list[dict]:
    group_expr = {
        "none": None,
        "route": "route",
        "hour": "time_bucket('1 hour', {t})",
        "day": "time_bucket('1 day', {t})",
    }[group_by]

    if await queries.sketches_available(pool):
        source, t = "api_latency_1min", "bucket"
        measures = """sum(request_count) AS requests, sum(error_count) AS errors,
               approx_percentile(0.50, rollup(latency)) AS p50_ms,
               approx_percentile(0.95, rollup(latency)) AS p95_ms,
               approx_percentile(0.99, rollup(latency)) AS p99_ms"""
    else:
        source, t = "request_events", "time"
        measures = """count(*) AS requests, count(*) FILTER (WHERE is_error) AS errors,
               percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms) AS p99_ms"""

    key = group_expr.format(t=t) if group_expr else "NULL"
    order = "requests DESC" if group_by == "route" else "1"
    rows = await pool.fetch(
        f"""
        SELECT {key} AS key, {measures}
        FROM {source}
        WHERE service_name = $1 AND {t} >= $2 AND {t} < $3
          AND ($4::text IS NULL OR route = $4)
          AND ($5::text IS NULL OR environment = $5)
        GROUP BY 1
        ORDER BY {order}
        LIMIT {MAX_GROUPS if group_by == "route" else MAX_SERIES_POINTS}
        """,
        service, start, end, route, environment,
    )
    out = []
    for r in rows:
        if not r["requests"]:
            continue
        item = _stats_row(r)
        if group_by == "route":
            item = {"route": r["key"], **item}
        elif group_by in ("hour", "day"):
            item = {group_by: _ts(r["key"]), **item}
        out.append(item)
    return out


def _retention(sketches: bool) -> timedelta:
    return AGGREGATE_RETENTION if sketches else RAW_RETENTION


async def get_stats(pool, service: str, args: dict, now: datetime) -> dict:
    group_by = args.get("group_by") or "none"
    if group_by not in GROUP_BY:
        raise ToolError(f"group_by must be one of {', '.join(GROUP_BY)}")
    start, end = time_range(args, now, _retention(await queries.sketches_available(pool)))
    if group_by == "hour" and end - start > timedelta(hours=MAX_SERIES_POINTS):
        raise ToolError(f"group_by=hour covers at most {MAX_SERIES_POINTS}h; narrow the range or use day")
    rows = await _stats(
        pool, service, start, end, args.get("route") or None, args.get("environment") or None, group_by
    )
    result = {"range": [_ts(start), _ts(end)], "route": args.get("route") or "all routes"}
    if group_by == "none":
        result["stats"] = rows[0] if rows else {"requests": 0}
    else:
        result["rows"] = rows
    return result


def _change(before: dict, after: dict) -> dict:
    change = {}
    for metric in ("error_rate", "p50_ms", "p95_ms", "p99_ms"):
        b, a = before.get(metric), after.get(metric)
        if b is not None and a is not None:
            change[metric] = {"delta": _num(a - b), "ratio": _num(a / b) if b else None}
    return change


MAX_CHANGED_ROUTES = 8


async def compare_periods(pool, service: str, args: dict, now: datetime) -> dict:
    retention = _retention(await queries.sketches_available(pool))
    route, environment = args.get("route") or None, args.get("environment") or None
    by_route = bool(args.get("by_route")) and route is None
    ranges = {
        label: time_range(args, now, retention, prefix=f"{label}_") for label in ("before", "after")
    }
    out = {"route": route or "all routes"}
    for label, (start, end) in ranges.items():
        rows = await _stats(pool, service, start, end, route, environment, "none")
        out[label] = {"range": [_ts(start), _ts(end)], **(rows[0] if rows else {"requests": 0})}
    out["change"] = _change(out["before"], out["after"])
    if not by_route:
        return out

    # Per route, the routes that changed most first: by error-rate increase,
    # then by p95 ratio.
    per_period = {
        label: {r["route"]: r for r in await _stats(pool, service, start, end, None, environment, "route")}
        for label, (start, end) in ranges.items()
    }
    routes = []
    for name, after in per_period["after"].items():
        before = per_period["before"].get(name)
        if before is None:
            routes.append({"route": name, "new_in_after": True, "after": after})
            continue
        change = _change(before, after)
        routes.append({"route": name, "before": before, "after": after, "change": change})

    def severity(item):
        change = item.get("change", {})
        return (
            (change.get("error_rate") or {}).get("delta") or 0,
            (change.get("p95_ms") or {}).get("ratio") or 0,
        )

    routes.sort(key=severity, reverse=True)
    out["routes_most_changed"] = routes[:MAX_CHANGED_ROUTES]
    return out


async def get_breakdown(pool, service: str, args: dict, now: datetime) -> dict:
    dimension = args.get("dimension")
    if dimension not in BREAKDOWN_DIMENSIONS:
        raise ToolError(f"dimension must be one of {', '.join(BREAKDOWN_DIMENSIONS)}")
    start, end = time_range(args, now, RAW_RETENTION)
    rows = await pool.fetch(
        f"""
        SELECT coalesce({dimension}::text, '(none)') AS value,
               count(*) AS requests,
               count(*) FILTER (WHERE is_error) AS errors,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
        FROM request_events
        WHERE service_name = $1 AND time >= $2 AND time < $3
          AND ($4::text IS NULL OR route = $4)
          AND ($5::text IS NULL OR environment = $5)
        GROUP BY 1
        ORDER BY requests DESC
        LIMIT {MAX_GROUPS}
        """,
        service, start, end, args.get("route") or None, args.get("environment") or None,
    )
    total = sum(r["requests"] for r in rows)
    total_errors = sum(r["errors"] for r in rows)
    values = [
        {
            "value": r["value"],
            "requests": r["requests"],
            "share_of_requests": _num(r["requests"] / total) if total else None,
            "errors": r["errors"],
            "share_of_errors": _num(r["errors"] / total_errors) if total_errors else None,
            "error_rate": _num(r["errors"] / r["requests"]),
            "p95_ms": _num(r["p95_ms"]),
        }
        for r in rows
    ]
    return {
        "range": [_ts(start), _ts(end)],
        "route": args.get("route") or "all routes",
        "dimension": dimension,
        "values": values,
    }


async def list_releases(pool, service: str, args: dict, now: datetime) -> dict:
    releases = await queries.list_releases(pool, service, limit=10)
    return {
        "releases": [
            {
                "release": r["release"],
                "first_seen": _ts(r["first_seen_at"]),
                "last_seen": _ts(r["last_seen_at"]),
                "environments": r["environments"],
                "requests_last_14d": r["request_count"],
                "error_rate": _num(r["error_rate"]),
                "p95_ms": _num(r["p95_ms"]),
            }
            for r in releases
        ]
    }


async def get_alerts(pool, service: str, args: dict, now: datetime) -> dict:
    status = args.get("status") or "all"
    if status not in ("open", "all"):
        raise ToolError("status must be open or all")
    rows = await pool.fetch(
        """
        SELECT kind, route, opened_at, last_hour, resolved_at, details
        FROM alerts
        WHERE service_name = $1 AND ($2 = 'all' OR resolved_at IS NULL)
        ORDER BY opened_at DESC
        LIMIT 15
        """,
        service, status,
    )
    alerts = []
    for r in rows:
        details = r["details"]
        if isinstance(details, str):
            details = json.loads(details)
        alert = {
            "kind": r["kind"],
            "route": r["route"],
            "opened": _ts(r["opened_at"]),
            "resolved": _ts(r["resolved_at"]) if r["resolved_at"] else None,
        }
        if r["kind"] == "slo":
            st = details.get("status", {})
            alert["slo"] = details.get("slo", {}).get("name")
            alert["state"] = st.get("state")
            alert["burn_rate_1h"] = st.get("burn_rates", {}).get("1h")
        else:
            for key in ("observed_error_rate", "baseline_error_rate", "observed_p95_ms",
                        "baseline_p95_ms", "z_score"):
                alert[key] = _num(details.get(key))
            alert["hour"] = _ts(r["last_hour"]) if r["last_hour"] else None
            ctx = details.get("release_context") or {}
            if ctx.get("is_new_release"):
                alert["new_release"] = ctx.get("release")
            alert["hints"] = [h.get("text") for h in details.get("hints") or []]
        alerts.append(alert)
    return {"status": status, "alerts": alerts}


async def get_slos(pool, service: str, args: dict, now: datetime) -> dict:
    slos = []
    for slo in await slo_status.list_slos(pool, service):
        st = await slo_status.slo_status(pool, slo, now)
        slos.append({
            "name": slo["name"],
            "route": slo["route"] or "all routes",
            "objective": slo["objective"],
            "target": slo["target"],
            "latency_threshold_ms": slo["latency_threshold_ms"],
            "window_days": slo["window_days"],
            "state": st["state"],
            "sli": _num(st["sli"], 6),
            "budget_remaining": _num(st["budget_remaining"]),
            "burn_rates": {k: _num(v) for k, v in st["burn_rates"].items()},
        })
    return {"slos": slos}


MAX_DRIFT_ITEMS = 10


async def get_api_drift(pool, service: str, args: dict, now: datetime) -> dict:
    report = await openapi_store.drift_report(pool, service, now)
    if report is None:
        return {"spec": None, "note": "no OpenAPI spec has been uploaded for this service"}

    def trim(items, keys):
        return [{k: _ts(v) if isinstance(v, datetime) else v for k, v in item.items() if k in keys}
                for item in items[:MAX_DRIFT_ITEMS]]

    return {
        "spec": {"title": report["spec"]["title"], "version": report["spec"]["version"],
                 "uploaded": _ts(report["spec"]["uploaded_at"])},
        "window_days": report["window_days"],
        "operations_in_spec": report["operations"],
        "operations_with_traffic": report["documented_in_use"],
        "coverage": _num(report["coverage"]),
        "undocumented_share_of_requests": _num(report["undocumented_requests"] / report["total_requests"])
        if report["total_requests"] else None,
        "unmatched_404_requests": report["unmatched_requests"],
        "undocumented": trim(report["undocumented"], {"method", "route", "requests", "error_rate", "last_seen"}),
        "undocumented_count": len(report["undocumented"]),
        "dead": trim(report["dead"], {"method", "path", "operation_id", "deprecated"}),
        "dead_count": len(report["dead"]),
        "deprecated_in_use": [
            {**item, "consumers": [c["consumer_id"] for c in op.get("consumers", [])]}
            for item, op in zip(
                trim(report["deprecated_in_use"], {"method", "path", "requests", "last_seen"}),
                report["deprecated_in_use"],
            )
        ],
    }


USAGE_WINDOWS = ("24h", "7d", "30d")


def _window_arg(args: dict) -> str:
    window = args.get("window") or "7d"
    if window not in USAGE_WINDOWS:
        raise ToolError(f"window must be one of {', '.join(USAGE_WINDOWS)}")
    return window


async def get_consumers(pool, service: str, args: dict, now: datetime) -> dict:
    window = _window_arg(args)
    consumer_id = args.get("consumer_id")
    if consumer_id:
        detail = await consumer_queries.consumer_detail(pool, service, consumer_id, window, now)
        if detail is None:
            return {"consumer_id": consumer_id, "note": "no traffic from this consumer in the window"}
        return {
            "consumer_id": consumer_id,
            "window": window,
            "routes": [
                {"method": r["method"], "route": r["route"], "requests": r["requests"],
                 "error_rate": _num(r["error_rate"]), "p95_ms": _num(r["p95_ms"])}
                for r in detail["routes"][:MAX_GROUPS]
            ],
        }
    top = await consumer_queries.top_consumers(pool, service, window, now)
    if not top["requests_with_consumer"]:
        return {"window": window, "note": "no request in the window carried a consumer id "
                "(the SDK's consumer_header / consumer option isn't set)"}
    return {
        "window": window,
        "consumers": top["consumers"],
        "share_of_requests_with_consumer": _num(top["requests_with_consumer"] / top["requests"]),
        "top": [
            {"consumer_id": c["consumer_id"], "requests": c["requests"],
             "share_of_requests": _num(c["share_of_requests"]), "errors": c["errors"],
             "error_rate": _num(c["error_rate"]), "routes": c["routes"]}
            for c in top["top"][:15]
        ],
    }


async def get_llm_costs(pool, service: str, args: dict, now: datetime) -> dict:
    window = _window_arg(args)
    usage = await llm_usage(pool, service, window, now)
    if not usage["totals"]["llm_requests"]:
        return {"window": window, "note": "no LLM usage recorded (reqly.record_llm_usage)"}
    return {
        "window": window,
        "prices_as_of": usage["prices_as_of"],
        "total_cost_usd": _num(usage["totals"]["cost_usd"]),
        "llm_requests": usage["totals"]["llm_requests"],
        "input_tokens": usage["totals"]["input_tokens"],
        "output_tokens": usage["totals"]["output_tokens"],
        "routes": [
            {"route": r["route"], "cost_usd": _num(r["cost_usd"]),
             "cost_per_1k_requests": _num(r["cost_per_1k_requests"]),
             "tokens_per_llm_request": _num(r["tokens_per_llm_request"]),
             "models": [m["model"] for m in r["models"]]}
            for r in usage["routes"][:MAX_GROUPS]
        ],
        "daily_cost_usd": [{"day": _ts(d["day"])[:10], "cost_usd": _num(d["cost_usd"])} for d in usage["daily"]],
        "unpriced_models": [m["model"] for m in usage["unpriced_models"]],
    }


_RANGE_PROPS = {
    "start": {"type": "string", "description": "ISO 8601 UTC start (inclusive). Default: end minus 24h."},
    "end": {"type": "string", "description": "ISO 8601 UTC end (exclusive). Default: now."},
}
_FILTER_PROPS = {
    "route": {"type": "string", "description": "Exact route template, e.g. /orders/{id}. Omit for all routes."},
    "environment": {"type": "string", "description": "Deployment environment, e.g. production. Omit for all."},
}


def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


TOOL_SCHEMAS = [
    _schema(
        "get_stats",
        "Request count, errors, error rate and p50/p95/p99 latency (ms) for a time range, "
        "optionally for one route, either as one total or grouped by route, hour or day.",
        {
            **_RANGE_PROPS,
            **_FILTER_PROPS,
            "group_by": {"type": "string", "enum": list(GROUP_BY), "description": "Default none (one total)."},
        },
    ),
    _schema(
        "compare_periods",
        "The same stats for two time ranges side by side, with the change -- e.g. before vs after a deploy, "
        "or yesterday vs the same hours a week earlier. With by_route, also lists the routes that changed most.",
        {
            "before_start": {"type": "string"}, "before_end": {"type": "string"},
            "after_start": {"type": "string"}, "after_end": {"type": "string"},
            **_FILTER_PROPS,
            "by_route": {"type": "boolean", "description": "Also compare route by route (only without route)."},
        },
        ["before_start", "before_end", "after_start", "after_end"],
    ),
    _schema(
        "get_breakdown",
        "Splits requests in a time range (last 14 days only) by one dimension -- host, environment, release, consumer_id, "
        "status_code, error_type or method -- with each value's share of traffic and of errors, error rate "
        "and p95. Use it to find what a problem is concentrated in.",
        {
            **_RANGE_PROPS,
            **_FILTER_PROPS,
            "dimension": {"type": "string", "enum": list(BREAKDOWN_DIMENSIONS)},
        },
        ["dimension"],
    ),
    _schema("list_releases", "Recent releases (deploys), newest first, with when each was first seen and its error rate and p95.", {}),
    _schema(
        "get_alerts",
        "Recent alerts: hourly anomalies (with baseline, z-score, release and root-cause hints) and SLO burn alerts.",
        {"status": {"type": "string", "enum": ["open", "all"], "description": "Default all (recent, incl. resolved)."}},
    ),
    _schema("get_slos", "The service's SLOs with SLI, error budget remaining, burn rates and state.", {}),
    _schema(
        "get_consumers",
        "API consumers (hashed client ids sent by the SDK): the top ones by requests with error rates, or "
        "one consumer's routes when consumer_id is given.",
        {
            "window": {"type": "string", "enum": ["24h", "7d", "30d"], "description": "Default 7d."},
            "consumer_id": {"type": "string"},
        },
    ),
    _schema(
        "get_llm_costs",
        "LLM token usage and estimated cost (USD, list prices) per route and per day.",
        {"window": {"type": "string", "enum": ["24h", "7d", "30d"], "description": "Default 7d."}},
    ),
    _schema(
        "get_api_drift",
        "Compares the service's uploaded OpenAPI spec with the last 30 days of traffic: undocumented "
        "endpoints (called but not in the spec), dead ones (in the spec, never called) and deprecated "
        "endpoints still in use.",
        {},
    ),
]

TOOLS = {
    "get_stats": get_stats,
    "compare_periods": compare_periods,
    "get_breakdown": get_breakdown,
    "list_releases": list_releases,
    "get_alerts": get_alerts,
    "get_slos": get_slos,
    "get_api_drift": get_api_drift,
    "get_consumers": get_consumers,
    "get_llm_costs": get_llm_costs,
}


async def run_tool(pool: asyncpg.Pool, service: str, name: str, args: dict, now: datetime) -> dict:
    """Runs one tool call; argument problems come back as {"error": ...}."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"unknown tool {name!r}; available: {', '.join(TOOLS)}"}
    if not isinstance(args, dict):
        return {"error": "arguments must be a JSON object"}
    try:
        return await tool(pool, service, args, now)
    except ToolError as exc:
        return {"error": str(exc)}
