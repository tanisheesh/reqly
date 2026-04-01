from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query

from ..auth import verify_read_key
from ..db import queries
from ..db.pool import get_pool

router = APIRouter(dependencies=[Depends(verify_read_key)])


@router.get("/v1/services")
async def services():
    pool = get_pool()
    return {"services": await queries.list_services(pool)}


@router.get("/v1/services/{service_name}/routes")
async def routes(service_name: str):
    pool = get_pool()
    return {"routes": await queries.list_routes(pool, service_name)}


@router.get("/v1/metrics/summary")
async def metrics_summary(
    service_name: str,
    route: str | None = None,
    window: str = Query(default="1h", pattern="^(1h|6h|24h|7d)$"),
):
    pool = get_pool()
    latency, error_rate, status_distribution, top_routes, request_rate = await asyncio.gather(
        queries.get_latency_series(pool, service_name, route, window),
        queries.get_error_rate_series(pool, service_name, route, window),
        queries.get_status_distribution(pool, service_name, route, window),
        queries.get_top_routes(pool, service_name, window),
        queries.get_request_rate(pool, service_name),
    )

    return {
        "service_name": service_name,
        "route": route,
        "window": window,
        "latency": latency,
        "error_rate": error_rate,
        "status_distribution": status_distribution,
        "top_routes": top_routes,
        "request_rate": request_rate,
    }
