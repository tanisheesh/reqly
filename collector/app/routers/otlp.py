"""OTLP/HTTP trace receiver: lets any OpenTelemetry-instrumented app report to
Reqly without a Reqly SDK.

Point an exporter at it with:
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://<collector>/otlp/v1/traces
    OTEL_EXPORTER_OTLP_HEADERS=x-reqly-key=<ingest key>
(or OTEL_EXPORTER_OTLP_ENDPOINT=https://<collector>/otlp -- exporters append
/v1/traces themselves). See https://reqly.tanisheesh.in/docs/instrument/opentelemetry/
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

from ..auth import Principal, verify_ingest_key, writable_service
from ..config import settings
from ..consumers.cap import consumer_cap
from ..db.late_data import event_time_problem
from ..db.late_data import tracker as late_data_tracker
from ..db.pool import get_pool
from ..db.queries import EVENT_COLUMNS, insert_events, insert_llm_splits, record_deployments, row_time
from ..gzip_body import decompress
from ..observability import INGEST_EVENTS
from ..otlp.mapping import map_export_request, map_resource_spans
from ..rate_limit import INGEST_IP_LIMIT, ingest_key, ingest_limit, limiter

router = APIRouter()

_SERVICE_INDEX = EVENT_COLUMNS.index("service_name")
_CONSUMER_INDEX = EVENT_COLUMNS.index("consumer_id")
_EVENT_ID_INDEX = EVENT_COLUMNS.index("event_id")

# Compressed request bodies are capped before reading, decompressed ones
# while inflating (gzip_body), so a small gzip bomb can't exhaust memory.
_MAX_BODY_BYTES = 8 * 1024 * 1024
_MAX_EVENTS_PER_REQUEST = 10_000

_PROTOBUF = "application/x-protobuf"
_JSON = "application/json"


def _respond(content_type: str, rejected: int, message: str) -> Response:
    if content_type == _PROTOBUF:
        response = ExportTraceServiceResponse()
        if rejected:
            response.partial_success.rejected_spans = rejected
            response.partial_success.error_message = message
        return Response(content=response.SerializeToString(), media_type=_PROTOBUF)
    body = {}
    if rejected:
        body = {"partialSuccess": {"rejectedSpans": str(rejected), "errorMessage": message}}
    return Response(content=json.dumps(body), media_type=_JSON)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _cap_consumers(pool, rows: list[tuple]) -> list[tuple]:
    """The per-service daily cap on distinct consumers, as on /v1/ingest."""
    by_service: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        if row[_CONSUMER_INDEX] is not None:
            by_service.setdefault(row[_SERVICE_INDEX], []).append(i)
    rows = list(rows)
    for service, indexes in by_service.items():
        capped = await consumer_cap.apply(
            pool, service, [(row_time(rows[i]), rows[i][_CONSUMER_INDEX]) for i in indexes],
            settings.consumer_limit_per_day,
        )
        for i, consumer in zip(indexes, capped):
            fields = list(rows[i])
            fields[_CONSUMER_INDEX] = consumer
            rows[i] = tuple(fields)
    return rows


@router.post("/otlp/v1/traces")
@limiter.limit(INGEST_IP_LIMIT)
@limiter.limit(ingest_limit, key_func=ingest_key)
async def export_traces(request: Request, principal: Principal = Depends(verify_ingest_key)) -> Response:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in (_PROTOBUF, _JSON):
        raise HTTPException(
            status_code=415, detail=f"Content-Type must be {_PROTOBUF} or {_JSON}"
        )

    declared_length = request.headers.get("content-length")
    if declared_length and declared_length.isdigit() and int(declared_length) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request body too large")
    body = await request.body()
    if len(body) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request body too large")
    body = decompress(body, request.headers.get("content-encoding") or "")

    if content_type == _PROTOBUF:
        message = ExportTraceServiceRequest()
        try:
            message.ParseFromString(body)
        except DecodeError:
            raise HTTPException(status_code=400, detail="invalid protobuf body")
        result = map_export_request(message, settings.consumer_salt)
    else:
        try:
            payload = json.loads(body or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON body")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        result = map_resource_spans(payload, settings.consumer_salt)

    now = _now()
    accepted = []
    writable: dict[str, bool] = {}
    for row in result.rows:
        service = row[_SERVICE_INDEX]
        if service not in writable:
            writable[service] = await writable_service(principal, service)
        problem = (
            event_time_problem(row_time(row), now)
            or (None if writable[service] else f"service {service!r} belongs to another project")
        )
        if problem:
            result.reject(problem)
        else:
            accepted.append(row)
    result.rows = accepted
    if len(result.rows) > _MAX_EVENTS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f"more than {_MAX_EVENTS_PER_REQUEST} HTTP server spans in one request",
        )

    if result.rows:
        pool = get_pool()
        result.rows = await _cap_consumers(pool, result.rows)
        await insert_events(pool, result.rows)
        kept = {row[_EVENT_ID_INDEX] for row in result.rows}
        await insert_llm_splits(pool, [split for split in result.llm_splits if split[1] in kept])
        await record_deployments(pool, result.rows)
        late_data_tracker.note(min(row_time(r) for r in result.rows))

    INGEST_EVENTS.labels("otlp", "accepted").inc(len(result.rows))
    INGEST_EVENTS.labels("otlp", "rejected").inc(result.rejected)
    return _respond(content_type, result.rejected, "; ".join(result.reasons))
