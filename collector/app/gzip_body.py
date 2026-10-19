"""Request bodies sent with Content-Encoding: gzip.

The compressed body is already capped by BodySizeLimitMiddleware; the
decompressed one is capped while inflating, so a small gzip bomb can't
exhaust memory.
"""

from __future__ import annotations

import zlib

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute

MAX_DECOMPRESSED_BYTES = 32 * 1024 * 1024


def decompress(body: bytes, encoding: str) -> bytes:
    encoding = encoding.strip().lower()
    if encoding in ("", "identity"):
        return body
    if encoding != "gzip":
        raise HTTPException(status_code=415, detail=f"unsupported Content-Encoding: {encoding}")
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)  # gzip framing
    try:
        data = inflater.decompress(body, MAX_DECOMPRESSED_BYTES)
    except zlib.error:
        raise HTTPException(status_code=400, detail="invalid gzip body")
    if inflater.unconsumed_tail:
        raise HTTPException(status_code=413, detail="decompressed body too large")
    return data


class _GzipRequest(Request):
    async def body(self) -> bytes:
        if not hasattr(self, "_body"):
            raw = await super().body()
            self._body = decompress(raw, self.headers.get("content-encoding") or "")
        return self._body


class GzipRoute(APIRoute):
    """Route class whose JSON body may arrive gzip-compressed."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def route_handler(request: Request):
            return await handler(_GzipRequest(request.scope, request.receive))

        return route_handler
