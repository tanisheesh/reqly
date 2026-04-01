from __future__ import annotations

from fastapi import Header, HTTPException, status

from .config import settings


async def verify_ingest_key(x_REQLY_key: str | None = Header(default=None)) -> None:
    if not x_REQLY_key or x_REQLY_key != settings.ingest_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Reqly-Key header",
        )


async def verify_read_key(x_REQLY_key: str | None = Header(default=None)) -> None:
    if not x_REQLY_key or x_REQLY_key != settings.read_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Reqly-Key header",
        )


# backwards-compat alias used by ingest router
verify_api_key = verify_ingest_key
