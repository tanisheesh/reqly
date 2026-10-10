"""Generic WSGI middleware, for frameworks without a dedicated integration
(Bottle, Pyramid, Falcon, CherryPy, a hand-written WSGI app...).

There is no portable way to learn a WSGI request's route template, so pass a
``route_resolver``: it gets the WSGI environ *after* the app handled the
request (frameworks record the matched route there) and returns the
template, or None. Without one every request is recorded as
``__unmatched__`` -- raw paths are never used, they would explode the
number of distinct routes.

    environ["bottle.route"].rule                   # Bottle
    environ["bfg.routes.route"].pattern            # Pyramid (URL dispatch)
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from ..core.capture import normalize_route
from ..core.client import ReqlyClient
from ..core.request_context import LazyHeaders, RequestInfo, begin_request, end_request

logger = logging.getLogger("reqly")

WSGIRouteResolver = Callable[[dict], Optional[str]]


def _environ_key(name: str) -> str:
    key = name.upper().replace("-", "_")
    return key if key in ("CONTENT_TYPE", "CONTENT_LENGTH") else "HTTP_" + key


def _environ_headers(environ: dict):
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            yield key[5:].replace("_", "-").lower(), value
        elif key in ("CONTENT_TYPE", "CONTENT_LENGTH") and value:
            yield key.replace("_", "-").lower(), value


def wsgi_request_info(environ: dict) -> RequestInfo:
    headers = LazyHeaders(lambda name: environ.get(_environ_key(name)) or None, lambda: _environ_headers(environ))
    return RequestInfo(
        method=environ.get("REQUEST_METHOD", "GET"),
        path=environ.get("PATH_INFO", "/"),
        headers=headers,
        raw=environ,
    )


class _Request:
    """One request's measurements, recorded exactly once: when the response
    iterable is closed (the server is done sending), or when the app raises."""

    def __init__(self, middleware: "ReqlyWSGIMiddleware", environ: dict) -> None:
        self.middleware = middleware
        self.environ = environ
        self.start = time.perf_counter()
        self.usage_token = begin_request()
        self.status_code = 500
        self.response_bytes = 0
        self.error_type: str | None = None
        self.done = False

    def start_response(self, start_response):
        def wrapped(status, headers, exc_info=None):
            try:
                self.status_code = int(str(status).split(" ", 1)[0])
            except ValueError:
                pass
            if exc_info is not None:
                return start_response(status, headers, exc_info)
            return start_response(status, headers)

        return wrapped

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        self.middleware._record(self)


class _ResponseIterator:
    def __init__(self, result, request: _Request) -> None:
        self._result = result
        self._iter = iter(result)
        self._request = request

    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = next(self._iter)
        except StopIteration:
            # Servers should call close(), but not every one does.
            self._request.finish()
            raise
        except Exception as exc:
            self._request.error_type = type(exc).__name__
            self._request.status_code = 500
            raise
        self._request.response_bytes += len(chunk)
        return chunk

    def close(self) -> None:
        try:
            close = getattr(self._result, "close", None)
            if close is not None:
                close()
        finally:
            self._request.finish()


class ReqlyWSGIMiddleware:
    def __init__(self, app, client: ReqlyClient, route_resolver: WSGIRouteResolver | None = None) -> None:
        self.app = app
        self.client = client
        self._route_resolver = route_resolver

    def __call__(self, environ, start_response):
        request = _Request(self, environ)
        try:
            result = self.app(environ, request.start_response(start_response))
        except Exception as exc:
            request.error_type = type(exc).__name__
            request.status_code = 500
            request.finish()
            raise
        return _ResponseIterator(result, request)

    def _record(self, request: _Request) -> None:
        try:
            self._record_unguarded(request)
        except Exception:
            logger.debug("reqly: could not record WSGI request", exc_info=True)

    def _record_unguarded(self, request: _Request) -> None:
        environ = request.environ
        duration_ms = (time.perf_counter() - request.start) * 1000
        llm = end_request(request.usage_token)
        template = None
        if self._route_resolver is not None:
            try:
                template = self._route_resolver(environ)
            except Exception:
                template = None
        content_length = environ.get("CONTENT_LENGTH")
        self.client.record_request(
            method=environ.get("REQUEST_METHOD", "GET"),
            route=normalize_route(environ.get("PATH_INFO", "/"), template),
            status_code=request.status_code,
            duration_ms=duration_ms,
            error=request.error_type is not None or request.status_code >= 500,
            error_type=request.error_type,
            request_bytes=int(content_length) if content_length and str(content_length).isdigit() else None,
            response_bytes=request.response_bytes,
            request_info=lambda: wsgi_request_info(environ),
            llm=llm,
        )
