from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from .config import settings
from .db.pool import close_pool, create_pool
from .insights.scheduler import start_scheduler
from .rate_limit import limiter
from .routers import ingest, insights, metrics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reqly.collector")

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    logger.info("Reqly collector: db pool ready")
    global _scheduler
    _scheduler = start_scheduler()
    logger.info("Reqly collector: weekly insights scheduler started")
    try:
        yield
    finally:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
        await close_pool()


app = FastAPI(title="Reqly Collector", version="0.1.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Reqly-Key"],
)

app.include_router(ingest.router)
app.include_router(metrics.router)
app.include_router(insights.router)


@app.get("/v1/health")
async def health():
    return {"status": "ok"}
