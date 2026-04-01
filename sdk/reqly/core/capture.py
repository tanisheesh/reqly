from __future__ import annotations

import socket
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as _pkg_version


def _get_sdk_version() -> str:
    try:
        return _pkg_version("reqly")
    except PackageNotFoundError:
        return "0.1.0"

_HOSTNAME = socket.gethostname()


@dataclass
class RequestEvent:
    """One captured request. ``route`` MUST be the normalized route template
    (e.g. "/users/{id}"), never the raw path (e.g. "/users/123") -- every
    downstream aggregate's cardinality depends on this.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    service_name: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    method: str = "GET"
    route: str = "/"
    status_code: int = 200
    duration_ms: float = 0.0
    error: bool = False
    error_type: str | None = None
    host: str = _HOSTNAME
    sdk_version: str = field(default_factory=_get_sdk_version)

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_route(raw_path: str, matched_template: str | None) -> str:
    """Return the route template if one was matched by the framework's
    router; otherwise collapse to a single bucket so unmatched paths
    (404s, scanners hitting random URLs) can't blow up cardinality.
    """
    if matched_template:
        return matched_template
    return "__unmatched__"


def build_event(
    *,
    service_name: str,
    method: str,
    route: str,
    status_code: int,
    duration_ms: float,
    error: bool,
    error_type: str | None,
    sdk_version: str,
) -> RequestEvent:
    return RequestEvent(
        service_name=service_name,
        method=method,
        route=route,
        status_code=status_code,
        duration_ms=duration_ms,
        error=error,
        error_type=error_type,
        sdk_version=sdk_version,
    )
