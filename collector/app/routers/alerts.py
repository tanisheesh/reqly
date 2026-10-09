from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from ..auth import verify_read_key
from ..db.pool import get_pool

router = APIRouter(dependencies=[Depends(verify_read_key)])


@router.get("/v1/alerts")
async def list_alerts(
    service_name: str | None = None,
    status: str = Query(default="open", pattern="^(open|all)$"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Open alerts (default) or the most recent ones, newest first."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id, service_name, route, opened_at, first_hour, last_hour, resolved_at, details
        FROM alerts
        WHERE ($1::text IS NULL OR service_name = $1)
          AND ($2 = 'all' OR resolved_at IS NULL)
        ORDER BY opened_at DESC
        LIMIT $3
        """,
        service_name,
        status,
        limit,
    )
    alerts = []
    for r in rows:
        alert = dict(r)
        if isinstance(alert["details"], str):
            alert["details"] = json.loads(alert["details"])
        alerts.append(alert)
    return {"alerts": alerts}
