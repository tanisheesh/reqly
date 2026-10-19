from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..alerts import actions
from ..auth import Principal, allowed_services, check_service, resolve, verify_read_key
from ..db.pool import get_pool

router = APIRouter(dependencies=[Depends(verify_read_key)])
# Writes: not behind the read check, which the admin-scoped ingest key fails.
actions_router = APIRouter()


@router.get("/v1/alerts")
async def list_alerts(
    service_name: str | None = None,
    status: str = Query(default="open", pattern="^(open|all)$"),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(verify_read_key),
):
    """Open alerts (default) or the most recent ones, newest first."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT a.id, a.kind, a.service_name, a.route, a.opened_at, a.first_hour, a.last_hour, a.resolved_at,
               a.details, a.acknowledged_at, a.acknowledged_by, a.silenced_until,
               EXISTS (
                   SELECT 1 FROM alert_mutes m
                   WHERE m.service_name = a.service_name AND m.muted_until > now()
                     AND (m.route IS NULL OR m.route = a.route) AND (m.kind IS NULL OR m.kind = a.kind)
               ) AS muted
        FROM alerts a
        WHERE ($1::text IS NULL OR a.service_name = $1)
          AND ($2 = 'all' OR a.resolved_at IS NULL)
          AND ($4::text[] IS NULL OR a.service_name = ANY($4::text[]))
        ORDER BY a.opened_at DESC
        LIMIT $3
        """,
        service_name,
        status,
        limit,
        None if (allowed := await allowed_services(principal)) is None else sorted(allowed),
    )
    alerts = []
    for r in rows:
        alert = dict(r)
        if isinstance(alert["details"], str):
            alert["details"] = json.loads(alert["details"])
        alerts.append(alert)
    return {"alerts": alerts}


# --- on-call actions --------------------------------------------------------------


async def _on_call(
    x_reqly_key: str | None = Header(default=None), authorization: str | None = Header(default=None),
) -> Principal:
    """Acting on alerts changes who gets paged, so the public read key can't:
    a signed-in user, or a key with the admin scope."""
    principal = await resolve(x_reqly_key, authorization)
    if principal.kind != "user" and not principal.can("admin"):
        raise HTTPException(status_code=403, detail="alert actions need a signed-in user or a key with the admin scope")
    return principal


def _actor(principal: Principal) -> str:
    return principal.user["username"] if principal.user else principal.kind


async def _open_alert_service(alert_id: int, principal: Principal) -> str:
    service = await get_pool().fetchval("SELECT service_name FROM alerts WHERE id = $1", alert_id)
    if service is None:
        raise HTTPException(status_code=404, detail="no such alert")
    await check_service(principal, service)
    return service


def _now() -> datetime:
    return datetime.now(timezone.utc)


@actions_router.post("/v1/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int, principal: Principal = Depends(_on_call)):
    """Someone is on it: no more "still firing" reminders (the resolve still notifies)."""
    await _open_alert_service(alert_id, principal)
    row = await actions.acknowledge(get_pool(), alert_id, _actor(principal), _now())
    if row is None:
        raise HTTPException(status_code=409, detail="the alert is already resolved")
    return dict(row)


class SilenceIn(BaseModel):
    hours: float = Field(ge=0, le=actions.MAX_SILENCE.total_seconds() / 3600)  # 0 lifts it


@actions_router.post("/v1/alerts/{alert_id}/silence")
async def silence_alert(alert_id: int, body: SilenceIn, principal: Principal = Depends(_on_call)):
    """No notifications at all for this alert for `hours` (0: until now)."""
    await _open_alert_service(alert_id, principal)
    until = _now() + timedelta(hours=body.hours) if body.hours else None
    row = await actions.silence(get_pool(), alert_id, until)
    if row is None:
        raise HTTPException(status_code=409, detail="the alert is already resolved")
    return dict(row)


@router.get("/v1/alert-mutes")
async def list_mutes(service_name: str | None = None, principal: Principal = Depends(verify_read_key)):
    allowed = await allowed_services(principal)
    services = None if allowed is None else sorted(allowed)
    if service_name:
        services = [service_name] if services is None or service_name in services else []
    return {"mutes": await actions.active_mutes(get_pool(), services, _now())}


class MuteIn(BaseModel):
    service_name: str = Field(min_length=1, max_length=128)
    route: str | None = Field(default=None, max_length=512)  # None: every route
    kind: str | None = Field(default=None, pattern="^(anomaly|llm_cost|slo|client_errors)$")  # None: every kind
    hours: float = Field(gt=0, le=actions.MAX_MUTE.total_seconds() / 3600)
    reason: str | None = Field(default=None, max_length=255)


@actions_router.post("/v1/alert-mutes")
async def add_mute(body: MuteIn, principal: Principal = Depends(_on_call)):
    """No notifications for the service (or route, or kind) for `hours`.
    Alerts still open and show on the dashboard."""
    await check_service(principal, body.service_name)
    until = _now() + timedelta(hours=body.hours)
    return await actions.add_mute(
        get_pool(), body.service_name, body.route or None, body.kind, until, body.reason, _actor(principal),
    )


@actions_router.delete("/v1/alert-mutes/{mute_id}")
async def remove_mute(mute_id: int, principal: Principal = Depends(_on_call)):
    pool = get_pool()
    service = await pool.fetchval("SELECT service_name FROM alert_mutes WHERE id = $1", mute_id)
    if service is None:
        raise HTTPException(status_code=404, detail="no such mute")
    await check_service(principal, service)
    await pool.execute("UPDATE alert_mutes SET muted_until = least(muted_until, now()) WHERE id = $1", mute_id)
    return {"removed": mute_id}
