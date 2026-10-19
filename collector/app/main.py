from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .body_limit import BodySizeLimitMiddleware
from .config import settings
from .dashboard import mount_dashboard
from .db.late_data import run_refresh_loop
from .db.pool import close_pool, create_pool, get_pool
from .insights.scheduler import start_scheduler
from .logs import configure_logging
from .observability import MetricsMiddleware
from .observability import router as observability_router
from .rate_limit import limiter
from .routers import alerts, ask, auth, ingest, insights, metrics, openapi, otlp, projects, slos, usage
from .users.accounts import bootstrap_admin

configure_logging(settings.log_format)
logger = logging.getLogger("reqly.collector")

_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = settings.missing_keys()
    if missing:
        raise RuntimeError(
            f"{' and '.join(missing)} must be set (or REQLY_DEMO_MODE=true for the public demo keys). "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    pool = await create_pool()
    logger.info("Reqly collector: db pool ready")
    await bootstrap_admin(pool, settings.admin_username, settings.admin_password)
    if not settings.public_dashboard and not await pool.fetchval("SELECT EXISTS (SELECT 1 FROM users)"):
        logger.warning(
            "PUBLIC_DASHBOARD is off but there are no users: nobody can read. "
            "Set REQLY_ADMIN_PASSWORD (or run `python -m app.users create-user`)."
        )
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


app = FastAPI(title="Reqly Collector", version="0.13.0", lifespan=lifespan)

app.state.limiter = limiter

# Outermost: no request body above 16 MB is buffered, whatever the route.
app.add_middleware(BodySizeLimitMiddleware)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # slowapi's narrower signature

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Reqly-Key", "Authorization"],
)
# Outside the others, so it also counts what they answer themselves (413, CORS).
app.add_middleware(MetricsMiddleware)

app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(otlp.router)
app.include_router(metrics.router)
app.include_router(insights.router)
app.include_router(alerts.router)
app.include_router(alerts.actions_router)
app.include_router(slos.router)
app.include_router(ask.router)
app.include_router(openapi.router)
app.include_router(usage.router)
app.include_router(projects.router)
app.include_router(observability_router)


@app.get("/v1/health")
async def health():
    """Liveness: the process is up. Doesn't touch the database."""
    return {"status": "ok"}


@app.get("/v1/ready")
async def ready():
    """Readiness: the database answers. For Kubernetes readiness probes and
    load balancers; a 503 takes the instance out of rotation, not restarts it."""
    try:
        await asyncio.wait_for(get_pool().fetchval("SELECT 1"), timeout=2)
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "unreachable"}, status_code=503)
    return {"status": "ready", "database": "ok"}


# Last, so every API route above matches first.
if settings.dashboard_dir:
    mount_dashboard(app, settings.dashboard_dir)
