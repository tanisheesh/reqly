"""Who may call what.

- Ingest (SDKs, OTLP exporters): the ingest key, X-Reqly-Key.
- Read (dashboard, scripts): a signed-in user's session token
  (Authorization: Bearer ...), or the read key while PUBLIC_DASHBOARD is on.
  The read key ships in the dashboard bundle, so it is public: turning
  PUBLIC_DASHBOARD off makes it worthless.
- Admin (SLOs, OpenAPI specs): the ingest key, or an admin's session.

Sessions are bearer tokens rather than cookies: the dashboard and the
collector usually live on different sites (two *.onrender.com hosts are
different sites), where browsers block third-party cookies.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from .config import settings
from .db.pool import get_pool
from .users import accounts


def _key_matches(provided: str | None, expected: str) -> bool:
    # Constant-time comparison so the key can't be recovered byte-by-byte
    # from response timing.
    return bool(provided) and hmac.compare_digest(provided.encode(), expected.encode())


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def _session_user(authorization: str | None) -> dict | None:
    token = bearer_token(authorization)
    if token is None:
        return None
    user = await accounts.session_user(get_pool(), token)
    if user is None:
        raise _unauthorized("session expired or signed out; sign in again")
    return user


async def verify_ingest_key(x_REQLY_key: str | None = Header(default=None)) -> None:
    if not _key_matches(x_REQLY_key, settings.ingest_key):
        raise _unauthorized("invalid or missing X-Reqly-Key header")


async def verify_read_key(
    x_REQLY_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict | None:
    """Read access: a session, or the read key on a public dashboard.
    Returns the signed-in user, if any."""
    user = await _session_user(authorization)
    if user is not None:
        return user
    if settings.public_dashboard and _key_matches(x_REQLY_key, settings.read_key):
        return None
    if _key_matches(x_REQLY_key, settings.read_key):
        raise _unauthorized("this dashboard requires signing in")
    raise _unauthorized("invalid or missing X-Reqly-Key header")


async def verify_admin(
    x_REQLY_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Changing configuration: the ingest key (CI, scripts) or an admin."""
    user = await _session_user(authorization)
    if user is not None:
        if not user["is_admin"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admins only")
        return
    if not _key_matches(x_REQLY_key, settings.ingest_key):
        raise _unauthorized("invalid or missing X-Reqly-Key header")


async def current_user(authorization: str | None = Header(default=None)) -> dict:
    user = await _session_user(authorization)
    if user is None:
        raise _unauthorized("sign in first")
    return user


# backwards-compat alias used by the ingest router
verify_api_key = verify_ingest_key
