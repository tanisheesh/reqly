from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter, ValidationError, field_validator, model_validator

from ..auth import Principal, verify_ingest_key, writable_service
from ..config import settings
from ..consumers.cap import consumer_cap
from ..db.late_data import event_time_problem
from ..db.late_data import tracker as late_data_tracker
from ..db.pool import get_pool
from ..db.queries import event_row, insert_events, insert_llm_splits, record_deployments, row_time
from ..gzip_body import GzipRoute
from ..llm.splits import MAX_MODELS_PER_REQUEST, split_rows, summarize_models
from ..observability import INGEST_EVENTS
from ..rate_limit import INGEST_IP_LIMIT, ingest_key, ingest_limit, limiter
from ..traces import keep_trace_id, normalize_trace_id

# The SDKs gzip larger batches (Content-Encoding: gzip).
router = APIRouter(route_class=GzipRoute)

_MAX_BATCH_SIZE = 1000
_MAX_DURATION_MS = 300_000  # 5 minutes -- anything longer is clearly corrupt
# Length caps keep a buggy or hostile client from writing unbounded strings
# into indexed TEXT columns (route and service_name are in every aggregate).
_MAX_SERVICE_NAME_LEN = 128
_MAX_ROUTE_LEN = 512
_MAX_METHOD_LEN = 16
_MAX_SHORT_TEXT_LEN = 255
_MAX_RELEASE_LEN = 128
_MAX_ENVIRONMENT_LEN = 32
_MAX_CONSUMER_ID_LEN = 128
_MAX_BYTES = 10 * 1024**3  # 10 GiB -- larger is clearly corrupt
_MAX_TOKENS = 10_000_000


class LlmModelUsageIn(BaseModel):
    model: str = Field(min_length=1, max_length=_MAX_SHORT_TEXT_LEN)
    input_tokens: int = Field(default=0, ge=0, le=_MAX_TOKENS)
    output_tokens: int = Field(default=0, ge=0, le=_MAX_TOKENS)


class EventIn(BaseModel):
    event_id: uuid.UUID
    timestamp: AwareDatetime
    method: str = Field(min_length=1, max_length=_MAX_METHOD_LEN)
    route: str = Field(min_length=1, max_length=_MAX_ROUTE_LEN)
    status_code: int = Field(ge=100, le=599)
    duration_ms: float
    error: bool
    error_type: str | None = Field(default=None, max_length=_MAX_SHORT_TEXT_LEN)
    host: str | None = Field(default=None, max_length=_MAX_SHORT_TEXT_LEN)
    # --- v2 (all optional; spec: https://reqly.tanisheesh.in/docs/reference/ingest-spec/) ---
    release: str | None = Field(default=None, max_length=_MAX_RELEASE_LEN)
    environment: str | None = Field(default=None, max_length=_MAX_ENVIRONMENT_LEN)
    consumer_id: str | None = Field(default=None, max_length=_MAX_CONSUMER_ID_LEN)
    request_bytes: int | None = Field(default=None, ge=0, le=_MAX_BYTES)
    response_bytes: int | None = Field(default=None, ge=0, le=_MAX_BYTES)
    llm_model: str | None = Field(default=None, max_length=_MAX_SHORT_TEXT_LEN)
    llm_input_tokens: int | None = Field(default=None, ge=0, le=_MAX_TOKENS)
    llm_output_tokens: int | None = Field(default=None, ge=0, le=_MAX_TOKENS)
    # v3: each model's own tokens when the request called several. When
    # present it is the source of truth: llm_model / llm_*_tokens are
    # recomputed from it (main model, totals) so the two can't disagree.
    llm_usage: list[LlmModelUsageIn] | None = Field(default=None, max_length=MAX_MODELS_PER_REQUEST)
    # v4: the request's trace id (W3C traceparent, 16 or 32 hex digits). A
    # malformed one is dropped, not the event.
    trace_id: str | None = None

    @field_validator("duration_ms")
    @classmethod
    def clamp_duration(cls, v: float) -> float:
        if v < 0 or v > _MAX_DURATION_MS:
            raise ValueError("duration_ms out of plausible range")
        return v

    @field_validator("trace_id", mode="before")
    @classmethod
    def valid_trace_id(cls, v):
        return normalize_trace_id(v)

    @model_validator(mode="after")
    def totals_from_llm_usage(self) -> EventIn:
        if self.llm_usage:
            model, tokens_in, tokens_out, _ = summarize_models(
                [(m.model, m.input_tokens, m.output_tokens) for m in self.llm_usage]
            )
            if tokens_in > _MAX_TOKENS or tokens_out > _MAX_TOKENS:
                raise ValueError("llm_usage token totals out of plausible range")
            self.llm_model, self.llm_input_tokens, self.llm_output_tokens = model, tokens_in, tokens_out
        return self

    def per_model_usage(self) -> list[tuple[str, int, int]]:
        if not self.llm_usage:
            return []
        return summarize_models([(m.model, m.input_tokens, m.output_tokens) for m in self.llm_usage])[3]


# Validates a whole batch in one pydantic-core call; per-event validation
# only runs when this fails, to find which events to drop.
_BATCH_ADAPTER = TypeAdapter(list[EventIn])


def _validate_events(raw_events: list[dict]) -> tuple[list[EventIn], int, list[str]]:
    """Returns (valid events, rejected count, up to 5 reasons)."""
    try:
        return _BATCH_ADAPTER.validate_python(raw_events), 0, []
    except ValidationError:
        pass
    valid: list[EventIn] = []
    rejected = 0
    reasons: list[str] = []
    for raw_event in raw_events:
        try:
            valid.append(EventIn.model_validate(raw_event))
        except ValidationError as exc:
            rejected += 1
            if len(reasons) < 5:
                first = exc.errors()[0]
                reasons.append(f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}")
    return valid, rejected, reasons


class IngestRequest(BaseModel):
    service_name: str = Field(max_length=_MAX_SERVICE_NAME_LEN)
    sdk_version: str | None = None
    # Batch-level defaults (v2): an SDK knows its release/environment once
    # per process, so it sends them here instead of on every event. A value
    # on the event itself wins.
    release: str | None = Field(default=None, max_length=_MAX_RELEASE_LEN)
    environment: str | None = Field(default=None, max_length=_MAX_ENVIRONMENT_LEN)
    # Historical import: allows events older than the raw retention window.
    # The sender vouches that each hour it sends is complete, because those
    # hours are rebuilt in the aggregates from exactly what it sends.
    backfill: bool = False
    events: list[dict] = Field(min_length=1, max_length=_MAX_BATCH_SIZE)


@router.post("/v1/ingest")
@limiter.limit(INGEST_IP_LIMIT)
@limiter.limit(ingest_limit, key_func=ingest_key)
async def ingest(request: Request, body: IngestRequest, principal: Principal = Depends(verify_ingest_key)):
    """Batch ingestion with true partial-batch acceptance: each event is
    validated independently, so one malformed event from a buggy SDK only
    drops itself, not the whole batch of otherwise-good telemetry.
    """
    if not body.service_name:
        raise HTTPException(status_code=422, detail="service_name is required")
    if body.backfill and not principal.all_projects:
        # A backfill rebuilds its hours in aggregates shared by every
        # service and project, so only the collector's own key may send one.
        raise HTTPException(status_code=403, detail="backfill batches need the collector's REQLY_INGEST_KEY")
    if not await writable_service(principal, body.service_name):
        raise HTTPException(status_code=403, detail="this service belongs to another project")

    parsed, rejected, reasons = _validate_events(body.events)
    valid: list[EventIn] = []
    now = datetime.now(timezone.utc)
    for e in parsed:
        problem = event_time_problem(e.timestamp, now, body.backfill)
        if problem:
            rejected += 1
            if len(reasons) < 5:
                reasons.append(problem)
            continue
        valid.append(e)

    pool = get_pool()
    consumers = await consumer_cap.apply(
        pool, body.service_name, [(e.timestamp, e.consumer_id) for e in valid], settings.consumer_limit_per_day
    )
    rows = []
    splits: list[tuple] = []
    for e, consumer_id in zip(valid, consumers):
        splits += split_rows(str(e.event_id), e.timestamp, body.service_name, e.route, e.per_model_usage())
        rows.append(
            event_row(
                event_id=str(e.event_id),
                time=e.timestamp,
                service_name=body.service_name,
                method=e.method,
                route=e.route,
                status_code=e.status_code,
                duration_ms=e.duration_ms,
                is_error=e.error,
                error_type=e.error_type,
                host=e.host,
                release=e.release or body.release,
                environment=e.environment or body.environment,
                consumer_id=consumer_id,
                request_bytes=e.request_bytes,
                response_bytes=e.response_bytes,
                llm_model=e.llm_model,
                llm_input_tokens=e.llm_input_tokens,
                llm_output_tokens=e.llm_output_tokens,
                trace_id=keep_trace_id(e.trace_id, e.status_code, e.duration_ms),
            )
        )

    await insert_events(pool, rows)
    await insert_llm_splits(pool, splits)
    if rows:
        await record_deployments(pool, rows)
        if body.backfill:
            late_data_tracker.note_backfill(row_time(r) for r in rows)
        else:
            late_data_tracker.note(min(row_time(r) for r in rows))
    INGEST_EVENTS.labels("sdk", "accepted").inc(len(rows))
    INGEST_EVENTS.labels("sdk", "rejected").inc(rejected)
    response: dict[str, object] = {"accepted": len(rows), "rejected": rejected}
    if reasons:
        response["reasons"] = reasons
    return response
