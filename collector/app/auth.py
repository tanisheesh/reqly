from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from .config import settings


def _key_matches(provided: str | None, expected: str) -> bool:
    # Constant-time comparison so the key can't be recovered byte-by-byte
    # from response timing.
    return bool(provided) and hmac.compare_digest(provided.encode(), expected.encode())


async def verify_ingest_key(x_REQLY_key: str | None = Header(default=None)) -> None:
    if not _key_matches(x_REQLY_key, settings.ingest_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Reqly-Key header",
        )


async def verify_read_key(x_REQLY_key: str | None = Header(default=None)) -> None:
    if not _key_matches(x_REQLY_key, settings.read_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Reqly-Key header",
        )


# backwards-compat alias used by ingest router
verify_api_key = verify_ingest_key
