from __future__ import annotations

import logging

from .core.client import ReqlyClient
from .core.config import Config, _get_sdk_version

__version__ = _get_sdk_version()

__all__ = ["instrument"]

logger = logging.getLogger("reqly")

# id(app) -> (app, client) for every instrumented app. Holding the app
# itself keeps its id from being reused by a new object, so a repeat
# instrument() call on the same app is recognized reliably -- without
# setting attributes on the app (Litestar apps use __slots__ and refuse).
_CLIENTS: dict[int, tuple[object, ReqlyClient]] = {}


def _detect_framework(app) -> str:
    # Walk the class hierarchy so subclasses of an app class (a project's
    # own `class App(FastAPI)`) are recognized too. FastAPI subclasses
    # Starlette, so it must be checked first.
    modules = [cls.__module__ or "" for cls in type(app).__mro__]
    for framework in ("fastapi", "litestar", "starlette", "flask"):
        if any(m == framework or m.startswith(framework + ".") for m in modules):
            return framework
    # Fallback to duck typing if the module names aren't conclusive.
    if hasattr(app, "add_middleware") and hasattr(app, "router"):
        return "starlette"
    if hasattr(app, "before_request") and hasattr(app, "wsgi_app"):
        return "flask"
    raise TypeError(
        "reqly.instrument(): could not detect framework for app of type "
        f"{type(app)!r}. Supported: FastAPI, Starlette, Litestar, Flask "
        "(Django: add reqly.integrations.django.ReqlyMiddleware to MIDDLEWARE)."
    )


def _enable_openapi_push(app, framework: str, client: ReqlyClient) -> None:
    if framework == "fastapi":
        client.enable_openapi_push(app.openapi)
    elif framework == "litestar":
        client.enable_openapi_push(lambda: app.openapi_schema.to_schema())
    else:
        logger.warning(
            "reqly: push_openapi needs an app that generates its own spec (FastAPI, Litestar); "
            "for %s, upload the spec with PUT /v1/services/<service>/openapi instead",
            framework,
        )


def instrument(
    app,
    *,
    service_name: str | None = None,
    collector_url: str | None = None,
    api_key: str | None = None,
    sample_rate: float | None = None,
    flush_interval_seconds: float | None = None,
    max_batch_size: int | None = None,
    max_queue_size: int | None = None,
    ignore_routes: list[str] | None = None,
    capture_request_body: bool | None = None,
    release: str | None = None,
    environment: str | None = None,
    push_openapi: bool | None = None,
) -> ReqlyClient | None:
    """Instrument a FastAPI, Starlette, Litestar or Flask app with one line.
    (Django: add ``reqly.integrations.django.ReqlyMiddleware`` to MIDDLEWARE.)

    Config resolution order for any omitted argument: explicit kwarg >
    environment variable (REQLY_*) > default. See core.config.Config
    for the full list of environment variables.

    ``push_openapi=True`` (or REQLY_PUSH_OPENAPI=true) uploads the app's
    OpenAPI spec (FastAPI, Litestar) to the collector on the first request,
    so the dashboard can show undocumented, unused and deprecated-but-used
    endpoints.

    This function itself is guarded: a failure to detect the framework or
    initialize the client is logged and the app is returned uninstrumented
    rather than raising, so adding Reqly can never be the reason an
    app fails to start.
    """
    entry = _CLIENTS.get(id(app))
    existing = entry[1] if entry is not None and entry[0] is app else None
    if existing is not None:
        # A second call would add a second middleware and a second flush
        # thread, double-counting every request.
        logger.warning("reqly: app is already instrumented, ignoring repeat instrument() call")
        return existing

    try:
        framework = _detect_framework(app)
        config = Config.resolve(
            service_name=service_name,
            collector_url=collector_url,
            api_key=api_key,
            sample_rate=sample_rate,
            flush_interval_seconds=flush_interval_seconds,
            max_batch_size=max_batch_size,
            max_queue_size=max_queue_size,
            ignore_routes=ignore_routes,
            capture_request_body=capture_request_body,
            release=release,
            environment=environment,
            push_openapi=push_openapi,
        )
        client = ReqlyClient(config)
        if config.push_openapi:
            _enable_openapi_push(app, framework, client)

        if config.capture_request_body:
            logger.warning(
                "reqly: capture_request_body=True is set but body capture is not yet "
                "implemented — request bodies will not be captured"
            )

        if framework == "fastapi":
            from .integrations.fastapi import instrument_fastapi

            instrument_fastapi(app, client)
        elif framework == "starlette":
            from .integrations.starlette import instrument_starlette

            instrument_starlette(app, client)
        elif framework == "litestar":
            from .integrations.litestar import instrument_litestar

            instrument_litestar(app, client)
        else:
            from .integrations.flask import instrument_flask

            instrument_flask(app, client)

        _CLIENTS[id(app)] = (app, client)
        return client
    except Exception:
        logger.warning(
            "reqly: instrument() failed, app will run uninstrumented",
            exc_info=True,
        )
        return None
