#!/usr/bin/env python3
"""Synthetic load generator for the Reqly demo.

Startup sequence:
  1. Wait until the collector's /v1/health returns 200.
  2. Backfill BACKFILL_WEEKS of historical data at BACKFILL_EVENTS_PER_HOUR
     events per route per hour — gives the AI Insights pipeline enough seasonal
     data to detect the Monday-morning /auth/login degradation scenario.
  3. Loop forever generating live traffic at LIVE_RPS requests per second.

Two synthetic services are instrumented: fastapi-demo and flask-demo.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reqly.load_gen")

COLLECTOR_URL          = os.environ.get("REQLY_COLLECTOR_URL", "http://collector:8000")
INGEST_KEY             = os.environ.get("REQLY_INGEST_KEY", "demo-key")
BACKFILL_WEEKS         = int(os.environ.get("BACKFILL_WEEKS", "8"))
BACKFILL_EVENTS_PER_HOUR = int(os.environ.get("BACKFILL_EVENTS_PER_HOUR", "30"))
LIVE_RPS               = float(os.environ.get("LIVE_RPS", "2"))
BATCH_SIZE             = 500

SERVICES = ["fastapi-demo", "flask-demo"]

# (route, method, p50_ms, p95_ms, baseline_error_rate)
ROUTES: list[tuple[str, str, float, float, float]] = [
    ("/users/{id}",    "GET",  80,   750,  0.010),
    ("/users",         "GET",  55,   380,  0.005),
    ("/users",         "POST", 130,  950,  0.020),
    ("/products/{id}", "GET",  50,   320,  0.008),
    ("/products",      "GET",  65,   480,  0.005),
    ("/auth/login",    "POST", 210,  1100, 0.030),
    ("/auth/logout",   "POST", 35,   180,  0.001),
    ("/orders/{id}",   "GET",  160,  1050, 0.015),
    ("/orders",        "POST", 290,  1900, 0.025),
    ("/health",        "GET",  4,    18,   0.000),
]

# Monday 08:00-09:00 degradation injected into /auth/login for interview demo.
_DEGRADE_ROUTE      = "/auth/login"
_DEGRADE_DOW        = 0   # Monday
_DEGRADE_HOUR       = 8
_DEGRADE_ERROR_RATE = 0.18
_DEGRADE_P95_MS     = 1800.0


def _sample_duration_ms(p50: float, p95: float) -> float:
    """Log-normal approximation that reproduces the target percentiles."""
    sigma = (math.log(p95) - math.log(p50)) / 1.645
    return max(1.0, random.lognormvariate(math.log(p50), sigma))


def _make_event(
    service_name: str,
    route: str,
    method: str,
    p50_ms: float,
    p95_ms: float,
    error_rate: float,
    ts: datetime,
) -> dict:
    if route == _DEGRADE_ROUTE and ts.weekday() == _DEGRADE_DOW and ts.hour == _DEGRADE_HOUR:
        error_rate = _DEGRADE_ERROR_RATE
        p95_ms     = _DEGRADE_P95_MS

    is_error    = random.random() < error_rate
    duration_ms = _sample_duration_ms(p50_ms, p95_ms)
    status_code = (
        random.choice([500, 502, 503]) if is_error
        else random.choices([200, 201, 204], weights=[8, 1, 1])[0]
    )
    return {
        "event_id":    str(uuid.uuid4()),
        "timestamp":   ts.isoformat(),
        "method":      method,
        "route":       route,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 2),
        "error":       is_error,
        "error_type":  "InternalServerError" if is_error else None,
        "host":        f"{service_name}-host",
    }


def _post_batch(service_name: str, events: list[dict]) -> bool:
    payload = json.dumps({
        "service_name": service_name,
        "sdk_version":  "0.1.3",
        "events":       events,
    }).encode()
    req = urllib.request.Request(
        f"{COLLECTOR_URL}/v1/ingest",
        data=payload,
        headers={"Content-Type": "application/json", "X-Reqly-Key": INGEST_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 400
    except Exception as exc:
        logger.warning("ingest failed: %s", exc)
        return False


def wait_for_collector() -> None:
    for attempt in range(1, 61):
        try:
            with urllib.request.urlopen(f"{COLLECTOR_URL}/v1/health", timeout=5) as resp:
                if resp.status == 200:
                    logger.info("collector is ready")
                    return
        except Exception:
            pass
        logger.info("waiting for collector (%d/60)…", attempt)
        time.sleep(2)
    raise RuntimeError("collector never became healthy after 120 s")


def backfill() -> None:
    now   = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = now - timedelta(weeks=BACKFILL_WEEKS)
    total_hours = int((now - start).total_seconds() // 3600)
    logger.info(
        "backfilling %d weeks (%d hours) × %d events/route/hour for %d services…",
        BACKFILL_WEEKS, total_hours, BACKFILL_EVENTS_PER_HOUR, len(SERVICES),
    )

    for service_name in SERVICES:
        batch: list[dict] = []
        shipped = 0

        for hour_offset in range(total_hours):
            hour_start = start + timedelta(hours=hour_offset)
            for route, method, p50, p95, err_rate in ROUTES:
                for _ in range(BACKFILL_EVENTS_PER_HOUR):
                    ts = hour_start + timedelta(seconds=random.randint(0, 3599))
                    batch.append(_make_event(service_name, route, method, p50, p95, err_rate, ts))
                    if len(batch) >= BATCH_SIZE:
                        _post_batch(service_name, batch)
                        shipped += len(batch)
                        batch = []

        if batch:
            _post_batch(service_name, batch)
            shipped += len(batch)

        logger.info("backfill done for %s — %d events sent", service_name, shipped)

    logger.info("backfill complete")


def live_loop() -> None:
    logger.info("starting live traffic at %.1f req/s across %d services", LIVE_RPS, len(SERVICES))
    sleep_s = 1.0 / LIVE_RPS
    while True:
        service_name            = random.choice(SERVICES)
        route, method, p50, p95, err_rate = random.choice(ROUTES)
        now = datetime.now(timezone.utc)
        event = _make_event(service_name, route, method, p50, p95, err_rate, now)
        _post_batch(service_name, [event])
        time.sleep(sleep_s)


if __name__ == "__main__":
    wait_for_collector()
    backfill()
    live_loop()
