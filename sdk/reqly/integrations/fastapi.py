from __future__ import annotations

import time

from ..core.capture import normalize_route
from ..core.client import ReqlyClient


class ReqlyASGIMiddleware:
    """Pure ASGI middleware (not Starlette's BaseHTTPMiddleware, which
    buffers streaming response bodies). Wraps ``send`` to intercept the
    ``http.response.start`` message for the status code, and measures
    duration at the point the response completes.

    The route template is only known AFTER the inner app has routed the
    request (Starlette sets ``scope["route"]`` during dispatch), so the
    timer starts before calling the inner app and the route is read from
    the (mutated in place) scope dict afterward.
    """

    def __init__(self, app, client: ReqlyClient) -> None:
        self.app = app
        self._client = client

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code = 500
        error = False
        error_type = None

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            error = True
            error_type = type(exc).__name__
            status_code = 500
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            route = scope.get("route")
            route_template = getattr(route, "path", None) if route else None
            normalized = normalize_route(scope.get("path", "/"), route_template)
            self._client.record_request(
                method=scope.get("method", "GET"),
                route=normalized,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error or status_code >= 500,
                error_type=error_type,
            )


def instrument_fastapi(app, client: ReqlyClient) -> None:
    app.add_middleware(ReqlyASGIMiddleware, client=client)
