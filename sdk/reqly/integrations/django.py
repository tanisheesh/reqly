"""Django integration (also covers Django REST Framework and Django Ninja,
which route through Django's URL resolver).

Django has no app object to pass to ``reqly.instrument()``; add the
middleware instead, as high in the list as possible so it times the whole
stack::

    MIDDLEWARE = [
        "reqly.integrations.django.ReqlyMiddleware",
        ...
    ]

    # Optional -- same options as reqly.instrument(); REQLY_* environment
    # variables still apply for anything not set here.
    REQLY = {"service_name": "checkout-api", "api_key": "..."}

Works under WSGI and ASGI: the middleware is sync- and async-capable, so it
never forces Django to switch modes.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.core.exceptions import MiddlewareNotUsed

from ..core.capture import normalize_route
from ..core.client import ReqlyClient
from ..core.config import Config
from ..core.request_context import LazyHeaders, RequestInfo, begin_request, end_request

logger = logging.getLogger("reqly")

_EXCEPTION_ATTR = "_reqly_exception"

_client: ReqlyClient | None = None
_client_lock = threading.Lock()

_CONFIG_KEYS = (
    "service_name", "collector_url", "api_key", "sample_rate", "flush_interval_seconds",
    "max_batch_size", "max_queue_size", "ignore_routes", "capture_request_body",
    "release", "environment", "consumer_header", "consumer", "consumer_salt", "hash_consumer",
    "push_openapi",
)

# <int:pk>, <slug:slug>, <pk>  ->  {pk}
_PATH_CONVERTER = re.compile(r"<(?:[^>:]+:)?([^>]+)>")
# (?P<pk>[^/.]+)  ->  {pk}   (re_path / DRF routers)
_REGEX_GROUP = re.compile(r"\(\?P<(\w+)>[^()]*(?:\([^()]*\)[^()]*)*\)")


def normalize_django_route(route: str) -> str:
    """Django's resolver_match.route -> the template style the other
    integrations use, so routes look alike across frameworks:

        "users/<int:pk>/"                 -> "/users/{pk}/"
        "^api/users/(?P<pk>[^/.]+)/$"     -> "/api/users/{pk}/"
    """
    # Regex groups first: their "(?P<name>" would otherwise look like a
    # path converter to the second pattern.
    template = _REGEX_GROUP.sub(r"{\1}", route)
    template = _PATH_CONVERTER.sub(r"{\1}", template)
    template = template.replace("^", "").replace("$", "").replace("\\", "")
    return template if template.startswith("/") else "/" + template


def _get_client() -> ReqlyClient | None:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            from django.conf import settings

            options = dict(getattr(settings, "REQLY", {}) or {})
            unknown = set(options) - set(_CONFIG_KEYS)
            if unknown:
                logger.warning("reqly: ignoring unknown REQLY settings %s", sorted(unknown))
            config = Config.resolve(**{key: options.get(key) for key in _CONFIG_KEYS})
            _client = ReqlyClient(config)
            from .. import enable_spec_push

            enable_spec_push(_client, "Django")
    return _client


class ReqlyMiddleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response) -> None:
        self.get_response = get_response
        try:
            self._client = _get_client()
        except Exception:
            logger.warning("reqly: failed to initialize, Django instrumentation disabled", exc_info=True)
            raise MiddlewareNotUsed("reqly could not be initialized")
        self._is_async = iscoroutinefunction(get_response)
        if self._is_async:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self._is_async:
            return self.__acall__(request)
        start = time.perf_counter()
        token = begin_request()
        try:
            response = self.get_response(request)
        finally:
            llm = end_request(token)
        self._record(request, response, start, llm)
        return response

    async def __acall__(self, request):
        start = time.perf_counter()
        token = begin_request()
        try:
            response = await self.get_response(request)
        finally:
            llm = end_request(token)
        self._record(request, response, start, llm)
        return response

    def process_exception(self, request, exception):
        # Django turns unhandled view exceptions into a 500 response before
        # it reaches this middleware's __call__; keep the exception type.
        setattr(request, _EXCEPTION_ATTR, exception)
        return None

    def _record(self, request, response, start: float, llm=None) -> None:
        try:
            duration_ms = (time.perf_counter() - start) * 1000
            match = getattr(request, "resolver_match", None)
            template = normalize_django_route(match.route) if match is not None and match.route else None
            exception = getattr(request, _EXCEPTION_ATTR, None)
            status_code = getattr(response, "status_code", 500)
            content_length = request.META.get("CONTENT_LENGTH")
            self._client.record_request(
                method=request.method,
                route=normalize_route(request.path, template),
                status_code=status_code,
                duration_ms=duration_ms,
                error=exception is not None or status_code >= 500,
                error_type=type(exception).__name__ if exception is not None else None,
                request_bytes=int(content_length) if content_length and content_length.isdigit() else None,
                response_bytes=None if getattr(response, "streaming", False) else len(response.content),
                request_info=lambda: RequestInfo(
                    method=request.method,
                    path=request.path,
                    headers=LazyHeaders(request.headers.get, request.headers.items),
                    raw=request,
                ),
                llm=llm,
            )
        except Exception:
            # record_request is already fail-open; this guards the attribute
            # reads above so a strange response object can't break a request.
            logger.debug("reqly: could not record Django request", exc_info=True)
