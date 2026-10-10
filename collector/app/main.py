from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from .config import settings
from .db.late_data import run_refresh_loop
from .db.pool import close_pool, create_pool
from .insights.scheduler import start_scheduler
from .rate_limit import limiter
from .routers import alerts, ask, ingest, insights, metrics, otlp, slos

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reqly.collector")

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await create_pool()
    logger.info("Reqly collector: db pool ready")
    late_data_task = asyncio.create_task(
        run_refresh_loop(pool, settings.late_data_refresh_seconds)
    )
    global _scheduler
    if settings.insights_scheduler_enabled or settings.alerts_enabled:
        _scheduler = start_scheduler(
            weekly=settings.insights_scheduler_enabled, hourly_alerts=settings.alerts_enabled
        )
    logger.info(
        "Reqly collector: weekly insights %s, hourly alerts %s",
        "on" if settings.insights_scheduler_enabled else "off",
        "on" if settings.alerts_enabled else "off",
    )
    try:
        yield
    finally:
        late_data_task.cancel()
        with suppress(asyncio.CancelledError):
            await late_data_task
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
        await close_pool()


app = FastAPI(title="Reqly Collector", version="0.6.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Reqly-Key"],
)

app.include_router(ingest.router)
app.include_router(otlp.router)
app.include_router(metrics.router)
app.include_router(insights.router)
app.include_router(alerts.router)
app.include_router(slos.router)
app.include_router(ask.router)


@app.get("/v1/health")
async def health():
    return {"status": "ok"}
