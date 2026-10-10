from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import verify_read_key
from ..consumers import queries as consumers
from ..db.pool import get_pool
from ..llm.usage import llm_usage

router = APIRouter(dependencies=[Depends(verify_read_key)])

_WINDOW = Query(default="7d", pattern="^(24h|7d|30d)$")


@router.get("/v1/services/{service_name}/consumers")
async def top_consumers(service_name: str, window: str = _WINDOW):
    """Top consumers (hashed ids sent by the SDK) by requests, with errors."""
    return await consumers.top_consumers(get_pool(), service_name, window)


@router.get("/v1/services/{service_name}/consumers/{consumer_id}")
async def consumer_detail(service_name: str, consumer_id: str, window: str = _WINDOW):
    detail = await consumers.consumer_detail(get_pool(), service_name, consumer_id, window)
    if detail is None:
        raise HTTPException(status_code=404, detail="no traffic from this consumer in the window")
    return detail


@router.get("/v1/services/{service_name}/llm-usage")
async def llm_cost(service_name: str, window: str = _WINDOW):
    """LLM tokens and estimated cost per route (prices: app/llm/llm_prices.yaml,
    overridable with LLM_PRICES_FILE)."""
    return await llm_usage(get_pool(), service_name, window)
