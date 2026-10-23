"""The collector's own metrics, in the Prometheus text format at /metrics.

Labels are bounded on purpose: the collector's route templates (never raw
paths) and the ingest source -- no service, project or consumer names, so a
scrape can't leak tenant data or blow up cardinality.
"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily

from .config import settings
from .db import pool as pool_module
from .db.late_data import tracker as late_data_tracker

UNMATCHED_ROUTE = "__unmatched__"

HTTP_REQUESTS = Counter(
    "reqly_http_requests_total", "HTTP requests served by the collector.", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "reqly_http_request_duration_seconds",
    "Time to serve an HTTP request, by route template.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
INGEST_EVENTS = Counter(
    "reqly_ingest_events_total",
    "Events received, by source (sdk: /v1/ingest, otlp: /otlp/v1/traces, proxy: /v1/ingest/logs) and outcome.",
    ["source", "outcome"],
)


class _StateCollector:
    """Gauges read at scrape time: the database pool and the late-data backlog."""

    def collect(self):
        pool = pool_module._pool
        size = GaugeMetricFamily("reqly_db_pool_connections", "Open database connections.", labels=["state"])
        if pool is not None:
            idle = pool.get_idle_size()
            size.add_metric(["idle"], idle)
            size.add_metric(["busy"], pool.get_size() - idle)
        yield size
        pending = late_data_tracker.pending_since
        yield GaugeMetricFamily(
            "reqly_late_data_pending_seconds",
            "Age of the oldest late event still waiting for an aggregate refresh (0: none).",
            value=0 if pending is None else max(0.0, time.time() - pending.timestamp()),
        )


REGISTRY.register(_StateCollector())


class MetricsMiddleware:
    """Counts and times every request by its route template."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = 500  # an exception before the response started
        start = time.perf_counter()

        async def send_with_status(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or UNMATCHED_ROUTE
            method = scope.get("method", "")
            HTTP_REQUESTS.labels(method, template, str(status)).inc()
            HTTP_DURATION.labels(method, template).observe(time.perf_counter() - start)


router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    if settings.metrics_token:
        supplied = request.headers.get("authorization") or ""
        if not hmac.compare_digest(supplied.encode(), f"Bearer {settings.metrics_token}".encode()):
            raise HTTPException(status_code=401, detail="metrics need Authorization: Bearer <METRICS_TOKEN>")
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
