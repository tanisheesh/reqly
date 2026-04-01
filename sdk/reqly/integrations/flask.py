from __future__ import annotations

import time

from ..core.capture import normalize_route
from ..core.client import ReqlyClient

_START_TIME_ATTR = "_REQLY_start_time"


def instrument_flask(app, client: ReqlyClient) -> None:
    """Flask is WSGI, not ASGI -- uses request lifecycle signals rather than
    wrapping ``app.wsgi_app`` directly. ``teardown_request`` matters
    specifically because it fires even on unhandled exceptions, which is
    exactly when error telemetry is most valuable.
    """
    from flask import g, request

    @app.before_request
    def _REQLY_before_request():
        setattr(g, _START_TIME_ATTR, time.perf_counter())

    @app.teardown_request
    def _REQLY_teardown_request(exc):
        start = getattr(g, _START_TIME_ATTR, None)
        if start is None:
            return
        duration_ms = (time.perf_counter() - start) * 1000

        route_template = None
        if request.url_rule is not None:
            route_template = request.url_rule.rule
        normalized = normalize_route(request.path, route_template)

        if exc is not None:
            status_code = 500
            error = True
            error_type = type(exc).__name__
        else:
            response = getattr(g, "_REQLY_response_status", None)
            status_code = response if response is not None else 200
            error = status_code >= 500
            error_type = None

        client.record_request(
            method=request.method,
            route=normalized,
            status_code=status_code,
            duration_ms=duration_ms,
            error=error,
            error_type=error_type,
        )

    @app.after_request
    def _REQLY_after_request(response):
        g._REQLY_response_status = response.status_code
        return response
