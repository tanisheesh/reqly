from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as _pkg_version


def _get_sdk_version() -> str:
    """Single source of truth for the SDK version: the installed package
    metadata. A source checkout that was never pip-installed has none, and
    reports that honestly rather than a hardcoded number that goes stale."""
    try:
        return _pkg_version("reqly")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Commit SHA variables set by common CI/CD and hosting platforms, checked in
# order when neither release= nor REQLY_RELEASE is given.
_RELEASE_ENV_VARS = (
    "GIT_COMMIT",  # Jenkins and many custom pipelines
    "GITHUB_SHA",  # GitHub Actions
    "CI_COMMIT_SHA",  # GitLab CI
    "RENDER_GIT_COMMIT",  # Render
    "VERCEL_GIT_COMMIT_SHA",  # Vercel
    "RAILWAY_GIT_COMMIT_SHA",  # Railway
    "HEROKU_SLUG_COMMIT",  # Heroku (dyno metadata)
    "SOURCE_VERSION",  # Heroku buildpacks
    "K_REVISION",  # Cloud Run / Knative revision name
)
_MAX_RELEASE_LEN = 128
_MAX_ENVIRONMENT_LEN = 32


def _detect_release() -> str | None:
    for name in ("REQLY_RELEASE",) + _RELEASE_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value[:_MAX_RELEASE_LEN]
    return None


@dataclass
class Config:
    """Resolved configuration for an instrumented app.

    Resolution order for any field passed as None to ``instrument()``:
    explicit kwarg > environment variable > default.
    """

    service_name: str
    collector_url: str = "http://localhost:8000"
    api_key: str | None = None
    sample_rate: float = 1.0
    flush_interval_seconds: float = 5.0
    max_batch_size: int = 200
    max_queue_size: int = 2000
    ignore_routes: list[str] = field(default_factory=lambda: ["/health", "/metrics"])
    capture_request_body: bool = False
    release: str | None = None
    environment: str | None = None
    push_openapi: bool = False
    sdk_version: str = field(default_factory=_get_sdk_version)

    @classmethod
    def resolve(
        cls,
        service_name: str | None,
        collector_url: str | None,
        api_key: str | None,
        sample_rate: float | None,
        flush_interval_seconds: float | None,
        max_batch_size: int | None,
        max_queue_size: int | None,
        ignore_routes: list[str] | None,
        capture_request_body: bool | None,
        release: str | None = None,
        environment: str | None = None,
        push_openapi: bool | None = None,
    ) -> "Config":
        import sys as _sys

        resolved_service_name = (
            service_name
            or os.environ.get("REQLY_SERVICE_NAME")
            or (os.path.basename(_sys.argv[0]) if _sys.argv and _sys.argv[0] else None)
            or "unnamed-service"
        )

        return cls(
            service_name=resolved_service_name,
            collector_url=(
                collector_url
                or os.environ.get("REQLY_COLLECTOR_URL")
                or "http://localhost:8000"
            ),
            api_key=api_key or os.environ.get("REQLY_API_KEY"),
            sample_rate=(
                sample_rate
                if sample_rate is not None
                else _env_float("REQLY_SAMPLE_RATE", 1.0)
            ),
            flush_interval_seconds=(
                flush_interval_seconds
                if flush_interval_seconds is not None
                else _env_float("REQLY_FLUSH_INTERVAL_SECONDS", 5.0)
            ),
            max_batch_size=(
                max_batch_size
                if max_batch_size is not None
                else _env_int("REQLY_MAX_BATCH_SIZE", 200)
            ),
            max_queue_size=(
                max_queue_size
                if max_queue_size is not None
                else _env_int("REQLY_MAX_QUEUE_SIZE", 2000)
            ),
            ignore_routes=(
                ignore_routes
                if ignore_routes is not None
                else [
                    r.strip()
                    for r in os.environ.get("REQLY_IGNORE_ROUTES", "/health,/metrics").split(",")
                    if r.strip()
                ]
            ),
            capture_request_body=(
                capture_request_body
                if capture_request_body is not None
                else _env_bool("REQLY_CAPTURE_REQUEST_BODY", False)
            ),
            release=(release[:_MAX_RELEASE_LEN] if release else _detect_release()),
            environment=(
                (environment or os.environ.get("REQLY_ENVIRONMENT") or "").strip()[
                    :_MAX_ENVIRONMENT_LEN
                ]
                or None
            ),
            push_openapi=(
                push_openapi
                if push_openapi is not None
                else _env_bool("REQLY_PUSH_OPENAPI", False)
            ),
        )
