from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import bearer_token, current_user
from ..config import settings
from ..db.pool import get_pool
from ..rate_limit import limiter
from ..users import accounts

router = APIRouter()

# Password guessing is slowed per IP; argon2 makes each guess costly too.
_LOGIN_RATE_LIMIT = "10/minute"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


@router.get("/v1/auth/config")
async def auth_config():
    """Public: tells the dashboard whether it may read without signing in."""
    return {"public_dashboard": settings.public_dashboard, "login": True}


@router.post("/v1/auth/login")
@limiter.limit(_LOGIN_RATE_LIMIT)
async def login(request: Request, body: LoginIn, user_agent: str | None = Header(default=None)):
    try:
        token, expires_at, user = await accounts.login(
            get_pool(), body.username, body.password,
            timedelta(hours=settings.session_ttl_hours), user_agent=user_agent,
        )
    except accounts.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return {"token": token, "expires_at": expires_at.isoformat(), "user": user}


@router.post("/v1/auth/logout")
async def logout(authorization: str | None = Header(default=None)):
    token = bearer_token(authorization)
    if token:
        await accounts.logout(get_pool(), token)
    return {"signed_out": True}


@router.get("/v1/auth/me")
async def me(user: dict = Depends(current_user)):
    return {"user": user, "public_dashboard": settings.public_dashboard}


@router.post("/v1/auth/password")
@limiter.limit(_LOGIN_RATE_LIMIT)
async def change_password(request: Request, body: PasswordIn, user: dict = Depends(current_user)):
    """Changes the signed-in user's password and signs out all their sessions."""
    try:
        await accounts.change_password(get_pool(), user["id"], body.current_password, body.new_password)
    except accounts.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"changed": True}
