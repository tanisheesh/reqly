"""Deploy markers: a release recorded when it ships (from CI, the `reqly`
CLI or the GitHub Action), not only when its first request arrives. The
charts' release markers, the releases table, alert release context and Ask
Reqly all read `deployments`, so a marker sent at deploy time puts the
release where it really started."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field

from ..auth import Principal, verify_ingest_key, writable_service
from ..db.pool import get_pool
from ..db.queries import upsert_deployment
from ..rate_limit import INGEST_IP_LIMIT, ingest_key, ingest_limit, limiter

router = APIRouter()

_MAX_FUTURE_SKEW = timedelta(minutes=15)
_MAX_AGE = timedelta(days=400)


class DeploymentIn(BaseModel):
    service_name: str = Field(min_length=1, max_length=128)
    release: str = Field(min_length=1, max_length=128)
    environment: str | None = Field(default=None, max_length=32)
    deployed_at: AwareDatetime | None = None  # default: now


@router.post("/v1/deployments")
@limiter.limit(INGEST_IP_LIMIT)
@limiter.limit(ingest_limit, key_func=ingest_key)
async def record_deployment(request: Request, body: DeploymentIn, principal: Principal = Depends(verify_ingest_key)):
    """Records (or widens) a release of a service. Repeating it is harmless:
    the earliest time a release was seen, deployed or served, wins."""
    now = datetime.now(timezone.utc)
    deployed_at = body.deployed_at or now
    if deployed_at > now + _MAX_FUTURE_SKEW or deployed_at < now - _MAX_AGE:
        raise HTTPException(status_code=422, detail="deployed_at must be within the last 400 days and not in the future")
    if not await writable_service(principal, body.service_name):
        raise HTTPException(status_code=403, detail="this service belongs to another project")
    row = await upsert_deployment(get_pool(), body.service_name, body.environment or "", body.release, deployed_at)
    return {**row, "environment": row["environment"] or None}
