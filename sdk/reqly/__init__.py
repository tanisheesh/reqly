from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from .core.client import ReqlyClient
from .core.config import Config

try:
    __version__ = _pkg_version("reqly")
except PackageNotFoundError:
    __version__ = "0.1.3"

__all__ = ["instrument"]

logger = logging.getLogger("reqly")

# Keep a reference on each instrumented app so repeated calls / shutdown
# hooks can find the client without the caller having to hold onto it.
_CLIENTS: dict[int, ReqlyClient] = {}


def _detect_framework(app) -> str:
    module = type(app).__module__ or ""
    if "flask" in module:
        return "flask"
    if "fastapi" in module or "starlette" in module:
        return "fastapi"
    # Fallback to duck typing if the module name isn't conclusive.
    if hasattr(app, "add_middleware"):
        return "fastapi"
    if hasattr(app, "before_request") and hasattr(app, "wsgi_app"):
        return "flask"
    raise TypeError(
        "reqly.instrument(): could not detect framework for app of type "
        f"{type(app)!r}. Supported: FastAPI, Flask."
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
) -> ReqlyClient | None:
    """Instrument a FastAPI or Flask app with one line.

    Config resolution order for any omitted argument: explicit kwarg >
    environment variable (REQLY_*) > default. See core.config.Config
    for the full list of environment variables.

    This function itself is guarded: a failure to detect the framework or
    initialize the client is logged and the app is returned uninstrumented
    rather than raising, so adding Reqly can never be the reason an
    app fails to start.
    """
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
        )
        client = ReqlyClient(config)

        if config.capture_request_body:
            logger.warning(
                "reqly: capture_request_body=True is set but body capture is not yet "
                "implemented — request bodies will not be captured"
            )

        if framework == "fastapi":
            from .integrations.fastapi import instrument_fastapi

            instrument_fastapi(app, client)
        else:
            from .integrations.flask import instrument_flask

            instrument_flask(app, client)

        _CLIENTS[id(app)] = client
        return client
    except Exception:
        logger.warning(
            "reqly: instrument() failed, app will run uninstrumented",
            exc_info=True,
        )
        return None
