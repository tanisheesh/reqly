from __future__ import annotations

import time
from typing import Callable, Optional

from ..core.capture import normalize_route
from ..core.client import ReqlyClient

RouteResolver = Callable[[dict], Optional[str]]


def route_template_from_scope(scope: dict) -> str | None:
    """Route template set by the framework while routing: FastAPI puts the
    matched APIRoute in scope["route"]; Litestar sets scope["path_template"]."""
    route = scope.get("route")
    template = getattr(route, "path", None) if route is not None else None
    return template or scope.get("path_template")


class ReqlyASGIMiddleware:
    """Pure ASGI middleware (not Starlette's BaseHTTPMiddleware, which
    buffers streaming response bodies). Wraps ``send`` to intercept the
    ``http.response.start`` message for the status code, and measures
    duration at the point the response completes.

    The route template is only known AFTER the inner app has routed the
    request (FastAPI sets ``scope["route"]``, Litestar
    ``scope["path_template"]`` during dispatch), so the timer starts before
    calling the inner app and the route is read from the (mutated in place)
    scope dict afterward. Frameworks that don't record the route in the
    scope (plain Starlette) pass a ``route_resolver``, called with a copy of
    the scope as it was before routing.

    Shared by the FastAPI, Starlette and Litestar integrations.
    """

    def __init__(self, app, client: ReqlyClient, route_resolver: RouteResolver | None = None) -> None:
        self.app = app
        self._client = client
        self._route_resolver = route_resolver

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Routers mutate the scope while dispatching (Mount rewrites
        # root_path), so resolvers get the scope as it arrived.
        original_scope = dict(scope) if self._route_resolver is not None else None
        start = time.perf_counter()
        status_code = 500
        error = False
        error_type = None
        # Counted from the actual ASGI messages rather than Content-Length,
        # so chunked/streamed bodies are measured too.
        request_bytes = 0
        response_bytes = 0

        async def receive_wrapper():
            nonlocal request_bytes
            message = await receive()
            if message["type"] == "http.request":
                request_bytes += len(message.get("body", b""))
            return message

        async def send_wrapper(message):
            nonlocal status_code, response_bytes
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as exc:
            error = True
            error_type = type(exc).__name__
            status_code = 500
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            route_template = route_template_from_scope(scope)
            if route_template is None and self._route_resolver is not None:
                route_template = self._route_resolver(original_scope)
            normalized = normalize_route(scope.get("path", "/"), route_template)
            self._client.record_request(
                method=scope.get("method", "GET"),
                route=normalized,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error or status_code >= 500,
                error_type=error_type,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
            )


def instrument_fastapi(app, client: ReqlyClient) -> None:
    app.add_middleware(ReqlyASGIMiddleware, client=client)
