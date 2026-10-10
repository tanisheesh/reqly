from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..ask.agent import AskError, ask
from ..auth import verify_read_key
from ..config import settings
from ..db import queries
from ..db.pool import get_pool
from ..rate_limit import limiter

router = APIRouter(dependencies=[Depends(verify_read_key)])

_ASK_RATE_LIMIT = "5/minute"


class AskIn(BaseModel):
    service_name: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=3, max_length=500)


class _DailyBudget:
    """Questions answered today, per collector process. In memory like the
    per-IP limiter -- a cost guard, not an exact quota."""

    def __init__(self) -> None:
        self.day: date | None = None
        self.used = 0

    def take(self, limit: int, today: date) -> bool:
        if self.day != today:
            self.day, self.used = today, 0
        if self.used >= limit:
            return False
        self.used += 1
        return True


_budget = _DailyBudget()


@router.post("/v1/ask")
@limiter.limit(_ASK_RATE_LIMIT)
async def ask_question(request: Request, body: AskIn):
    """Answers a question about one service's traffic from its own data."""
    if not settings.groq_api_key or settings.ask_daily_limit <= 0:
        raise HTTPException(status_code=503, detail="Ask Reqly is not enabled on this collector (set GROQ_API_KEY)")
    pool = get_pool()
    if body.service_name not in await queries.list_services(pool):
        raise HTTPException(status_code=404, detail="unknown service")
    now = datetime.now(timezone.utc)
    if not _budget.take(settings.ask_daily_limit, now.date()):
        raise HTTPException(status_code=429, detail="daily question limit reached; try again tomorrow")
    try:
        return await ask(pool, body.service_name, body.question.strip(), now)
    except AskError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
