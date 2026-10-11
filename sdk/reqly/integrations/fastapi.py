from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from ..core.capture import normalize_route
from ..core.client import ReqlyClient
from ..core.request_context import LazyHeaders, RequestInfo, begin_request, end_request

logger = logging.getLogger("reqly")

CLIENT_CLOSED_REQUEST = 499

RouteResolver = Callable[[dict], Optional[str]]


def route_template_from_scope(scope: dict, entry_root_path: str = "") -> str | None:
    """Route template set by the framework while routing: FastAPI (and
    Starlette 1.7+) put the matched route in scope["route"]; Litestar sets
    scope["path_template"].

    A route inside a Mount (FastAPI app.mount(), Starlette Mount) only knows
    its path relative to the mount, while routing appended the mount prefix
    to scope["root_path"]. Prepending what routing added to root_path gives
    the full template: Mount("/api/v1") + "/items/{id}" -> "/api/v1/items/{id}".
    """
    route = scope.get("route")
    template = getattr(route, "path", None) if route is not None else None
    if template:
        root_path = scope.get("root_path") or ""
        if root_path.startswith(entry_root_path):
            template = root_path[len(entry_root_path):] + template
        return template
    return scope.get("path_template")


def asgi_request_info(scope: dict) -> RequestInfo:
    raw_headers = scope.get("headers") or []

    def get_one(name: str):
        wanted = name.encode("latin-1")
        for key, value in raw_headers:
            if key.lower() == wanted:
                return value.decode("latin-1")
        return None

    headers = LazyHeaders(get_one, lambda: ((k.decode("latin-1"), v.decode("latin-1")) for k, v in raw_headers))
    return RequestInfo(method=scope.get("method", "GET"), path=scope.get("path", "/"), headers=headers, raw=scope)


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
        self.client = client
        self._route_resolver = route_resolver

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Routers mutate the scope while dispatching (Mount rewrites
        # root_path), so resolvers get the scope as it arrived.
        original_scope = dict(scope) if self._route_resolver is not None else None
        entry_root_path = scope.get("root_path") or ""
        start = time.perf_counter()
        status_code = 500
        response_started = False
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
            nonlocal status_code, response_bytes, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        usage_token = begin_request()
        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except asyncio.CancelledError:
            # The client went away (or the server is shutting down) before
            # the app answered: 499 Client Closed Request, as nginx logs it,
            # not a 500 -- nothing failed on the app's side.
            if not response_started:
                status_code = CLIENT_CLOSED_REQUEST
            raise
        except Exception as exc:
            error = True
            error_type = type(exc).__name__
            status_code = 500
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            llm = end_request(usage_token)
            try:
                self._record(scope, original_scope, entry_root_path, status_code, duration_ms,
                             error, error_type, request_bytes, response_bytes, llm)
            except Exception:
                # Fail-open: a broken route_resolver (user code with
                # instrument_asgi) or anything else here must never reach
                # the app -- the response has usually been sent already.
                logger.warning("reqly: could not record ASGI request", exc_info=True)

    def _record(self, scope, original_scope, entry_root_path, status_code, duration_ms,
                error, error_type, request_bytes, response_bytes, llm) -> None:
        route_template = route_template_from_scope(scope, entry_root_path)
        if route_template is None and self._route_resolver is not None:
            try:
                route_template = self._route_resolver(original_scope)
            except Exception:
                # Same as the WSGI wrapper: a failing resolver means "no
                # route", not a lost event and not an error in the app.
                logger.debug("reqly: route_resolver failed", exc_info=True)
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
            request_info=lambda: asgi_request_info(scope),
            llm=llm,
        )


def instrument_fastapi(app, client: ReqlyClient) -> None:
    app.add_middleware(ReqlyASGIMiddleware, client=client)
