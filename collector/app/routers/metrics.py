from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import Principal, allowed_services, verify_read_key
from ..db import queries
from ..db.pool import get_pool

router = APIRouter(dependencies=[Depends(verify_read_key)])


@router.get("/v1/services")
async def services(principal: Principal = Depends(verify_read_key)):
    pool = get_pool()
    names = await queries.list_services(pool)
    allowed = await allowed_services(principal)
    return {"services": names if allowed is None else [s for s in names if s in allowed]}


@router.get("/v1/services/{service_name}/routes")
async def routes(service_name: str):
    pool = get_pool()
    return {"routes": await queries.list_routes(pool, service_name)}


@router.get("/v1/services/{service_name}/environments")
async def environments(service_name: str):
    """Environments the service reported in the last 90 days, for the
    dashboard's environment filter."""
    return {"environments": await queries.list_environments(get_pool(), service_name)}


@router.get("/v1/services/{service_name}/releases")
async def releases(service_name: str):
    pool = get_pool()
    return {"releases": await queries.list_releases(pool, service_name)}


@router.get("/v1/metrics/summary")
async def metrics_summary(
    service_name: str,
    route: str | None = None,
    window: str = Query(default="1h", pattern="^(1h|6h|24h|7d)$"),
    environment: str | None = Query(default=None, max_length=32),
):
    pool = get_pool()
    environment = environment or None
    try:
        (
            latency,
            error_rate,
            status_distribution,
            top_routes,
            request_rate,
            release_markers,
        ) = await asyncio.gather(
            queries.get_latency_series(pool, service_name, route, window, environment),
            queries.get_error_rate_series(pool, service_name, route, window, environment),
            queries.get_status_distribution(pool, service_name, route, window, environment),
            queries.get_top_routes(pool, service_name, window, environment),
            queries.get_request_rate(pool, service_name, environment),
            queries.get_release_markers(pool, service_name, window, environment),
        )
    except queries.EnvironmentFilterUnavailable:
        raise HTTPException(
            status_code=422,
            detail="filtering by environment needs the TimescaleDB Toolkit (timescale/timescaledb-ha)",
        )

    return {
        "service_name": service_name,
        "route": route,
        "window": window,
        "environment": environment,
        "latency": latency,
        "error_rate": error_rate,
        "status_distribution": status_distribution,
        "top_routes": top_routes,
        "request_rate": request_rate,
        "releases": release_markers,
    }
