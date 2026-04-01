from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, ValidationError, field_validator

from ..auth import verify_api_key
from ..db.pool import get_pool
from ..db.queries import insert_events
from ..rate_limit import RATE_LIMIT, limiter

router = APIRouter()

_MAX_BATCH_SIZE = 1000
_MAX_DURATION_MS = 300_000  # 5 minutes -- anything longer is clearly corrupt


class EventIn(BaseModel):
    event_id: uuid.UUID
    timestamp: AwareDatetime
    method: str
    route: str
    status_code: int = Field(ge=100, le=599)
    duration_ms: float
    error: bool
    error_type: str | None = None
    host: str | None = None

    @field_validator("duration_ms")
    @classmethod
    def clamp_duration(cls, v: float) -> float:
        if v < 0 or v > _MAX_DURATION_MS:
            raise ValueError("duration_ms out of plausible range")
        return v


class IngestRequest(BaseModel):
    service_name: str
    sdk_version: str | None = None
    events: list[dict] = Field(min_length=1, max_length=_MAX_BATCH_SIZE)


@router.post("/v1/ingest", dependencies=[Depends(verify_api_key)])
@limiter.limit(RATE_LIMIT)
async def ingest(request: Request, body: IngestRequest):
    """Batch ingestion with true partial-batch acceptance: each event is
    validated independently, so one malformed event from a buggy SDK only
    drops itself, not the whole batch of otherwise-good telemetry.
    """
    if not body.service_name:
        raise HTTPException(status_code=422, detail="service_name is required")

    rows = []
    rejected = 0
    for raw_event in body.events:
        try:
            e = EventIn.model_validate(raw_event)
        except ValidationError:
            rejected += 1
            continue
        rows.append(
            (
                str(e.event_id),
                e.timestamp,
                body.service_name,
                e.method,
                e.route,
                e.status_code,
                e.duration_ms,
                e.error,
                e.error_type,
                e.host,
            )
        )

    pool = get_pool()
    await insert_events(pool, rows)
    return {"accepted": len(rows), "rejected": rejected}
