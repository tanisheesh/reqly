"""Caps request body size for every endpoint.

Starlette reads a body into memory before any handler can look at its
length, and a chunked request carries no Content-Length to check up
front -- so without this, one request (authenticated or not: the body of
/v1/ingest is parsed before its key is checked) could make the collector
buffer gigabytes. Bodies are counted as they arrive and the request is
answered with 413 as soon as the limit is crossed.
"""

from __future__ import annotations

import json

DEFAULT_MAX_BODY_BYTES = 16 * 1024 * 1024


class _BodyTooLarge(Exception):
    pass


async def _send_413(send) -> None:
    body = json.dumps({"detail": "request body too large"}).encode()
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    def __init__(self, app, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > self.max_bytes:
                    await _send_413(send)
                    return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await _send_413(send)
