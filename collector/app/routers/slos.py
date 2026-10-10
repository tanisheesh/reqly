from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from ..auth import Principal, allowed_services, check_service, verify_admin, verify_read_key
from ..db.pool import get_pool
from ..slo import status as slo_status

router = APIRouter()


class SloIn(BaseModel):
    service_name: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    route: str | None = Field(default=None, max_length=512)
    objective: Literal["availability", "latency"]
    target: float = Field(gt=0, lt=1)
    latency_threshold_ms: float | None = Field(default=None, gt=0)
    window_days: int = Field(default=28, ge=1, le=90)

    @model_validator(mode="after")
    def latency_needs_threshold(self):
        if self.objective == "latency" and self.latency_threshold_ms is None:
            raise ValueError("latency SLOs need latency_threshold_ms")
        return self


@router.get("/v1/slos")
async def list_slos(service_name: str | None = None, principal: Principal = Depends(verify_read_key)):
    """SLOs with their live status: SLI, error budget left, burn rates."""
    pool = get_pool()
    slos = await slo_status.list_slos(pool, service_name)
    allowed = await allowed_services(principal)
    if allowed is not None:
        slos = [s for s in slos if s["service_name"] in allowed]
    for slo in slos:
        slo["status"] = await slo_status.slo_status(pool, slo)
    return {"slos": slos}


# Managing SLOs needs the ingest key or an admin session: the read key ships in the
# dashboard's JavaScript and must not be able to change anything.
@router.put("/v1/slos")
async def put_slo(body: SloIn, principal: Principal = Depends(verify_admin)):
    """Create or update (by service_name + name)."""
    await check_service(principal, body.service_name)
    return await slo_status.upsert_slo(get_pool(), body.model_dump())


@router.delete("/v1/slos/{slo_id}")
async def delete_slo(slo_id: int, principal: Principal = Depends(verify_admin)):
    service = await get_pool().fetchval("SELECT service_name FROM slos WHERE id = $1", slo_id)
    if service is not None:
        await check_service(principal, service)
    if not await slo_status.delete_slo(get_pool(), slo_id):
        raise HTTPException(status_code=404, detail="no such SLO")
    return {"deleted": slo_id}
