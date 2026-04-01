from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import verify_read_key
from ..db import queries
from ..db.pool import get_pool
from ..insights.scheduler import run_insights_for_service
from ..rate_limit import limiter

router = APIRouter(dependencies=[Depends(verify_read_key)])

_INSIGHTS_RATE_LIMIT = "5/minute"


@router.get("/v1/insights/latest")
async def latest_insight(service_name: str):
    pool = get_pool()
    report = await queries.get_latest_insight_report(pool, service_name)
    if not report:
        raise HTTPException(
            status_code=404, detail="no insight report yet for this service"
        )
    anomalies = report["anomalies_json"]
    if isinstance(anomalies, str):
        anomalies = json.loads(anomalies)
    return {
        "service_name": report["service_name"],
        "week_start": report["week_start"].isoformat(),
        "anomalies_json": anomalies,
        "report_text": report["report_text"],
        "generated_at": report["generated_at"].isoformat(),
    }


@router.post("/v1/insights/generate")
@limiter.limit(_INSIGHTS_RATE_LIMIT)
async def generate_insight(request: Request, service_name: str):
    """Demo-convenience endpoint: bypasses the weekly scheduler."""
    return await run_insights_for_service(service_name)
