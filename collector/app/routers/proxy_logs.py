"""POST /v1/ingest/logs: reverse proxy access logs (see app/proxylogs.py)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import Principal, verify_ingest_key, writable_service
from ..db.late_data import event_time_problem
from ..db.late_data import tracker as late_data_tracker
from ..db.pool import get_pool
from ..db.queries import event_row, insert_events, record_deployments, row_time
from ..gzip_body import GzipRoute
from ..observability import INGEST_EVENTS
from ..openapi.drift import spec_operations
from ..openapi.store import get_spec
from ..proxylogs import MAX_LINES, LineError, event_id, parse_body, parse_line, route_template
from ..rate_limit import INGEST_IP_LIMIT, ingest_key, ingest_limit, limiter
from ..traces import keep_trace_id, normalize_trace_id

router = APIRouter(route_class=GzipRoute)


def _trace_id(traceparent: str | None) -> str | None:
    if not traceparent:
        return None
    parts = traceparent.split("-")
    return normalize_trace_id(parts[1]) if len(parts) >= 4 else None


@router.post("/v1/ingest/logs")
@limiter.limit(INGEST_IP_LIMIT)
@limiter.limit(ingest_limit, key_func=ingest_key)
async def ingest_logs(
    request: Request,
    service_name: str = Query(min_length=1, max_length=128),
    release: str | None = Query(default=None, max_length=128),
    environment: str | None = Query(default=None, max_length=32),
    principal: Principal = Depends(verify_ingest_key),
):
    """JSON access log lines (NDJSON or a JSON array; gzip welcome) for one
    service. Each line is used or rejected on its own, like /v1/ingest."""
    if not await writable_service(principal, service_name):
        raise HTTPException(status_code=403, detail="this service belongs to another project")
    try:
        entries = parse_body(await request.body())
    except LineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if len(entries) > MAX_LINES:
        raise HTTPException(status_code=413, detail=f"more than {MAX_LINES} log lines in one request")

    pool = get_pool()
    spec = await get_spec(pool, service_name)
    operations = spec_operations(spec["spec"], spec["base_path"] or "") if spec else []
    now = datetime.now(timezone.utc)
    rows: list[tuple] = []
    rejected = 0
    reasons: list[str] = []

    def reject(reason: str) -> None:
        nonlocal rejected
        rejected += 1
        if len(reasons) < 5:
            reasons.append(reason)

    for n, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            reject(f"line {n}: not a JSON object")
            continue
        try:
            line = parse_line(entry, now)
        except LineError as exc:
            reject(f"line {n}: {exc}")
            continue
        problem = event_time_problem(line.time, now)
        if problem:
            reject(f"line {n}: {problem}")
            continue
        rows.append(event_row(
            event_id=event_id(service_name, json.dumps(entry, sort_keys=True)),
            time=line.time,
            service_name=service_name,
            method=line.method,
            route=route_template(line.method, line.path, operations),
            status_code=line.status,
            duration_ms=line.duration_ms,
            is_error=line.status >= 500,
            host=line.host,
            release=release,
            environment=environment,
            request_bytes=line.request_bytes,
            response_bytes=line.response_bytes,
            trace_id=keep_trace_id(_trace_id(line.traceparent), line.status, line.duration_ms),
        ))

    await insert_events(pool, rows)
    if rows:
        await record_deployments(pool, rows)
        late_data_tracker.note(min(row_time(r) for r in rows))
    INGEST_EVENTS.labels("proxy", "accepted").inc(len(rows))
    INGEST_EVENTS.labels("proxy", "rejected").inc(rejected)
    response: dict[str, object] = {"accepted": len(rows), "rejected": rejected}
    if reasons:
        response["reasons"] = reasons
    return response
