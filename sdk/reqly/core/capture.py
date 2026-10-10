from __future__ import annotations

import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .config import _get_sdk_version

_HOSTNAME = socket.gethostname()


@dataclass
class RequestEvent:
    """One captured request. ``route`` MUST be the normalized route template
    (e.g. "/users/{id}"), never the raw path (e.g. "/users/123") -- every
    downstream aggregate's cardinality depends on this.

    The request path only stores the time as a float; the event id and the
    ISO timestamp are made when the batch is serialized on the flush thread
    (to_dict), which keeps uuid4 and datetime formatting off the request.
    The id is stored on first serialization, so retries resend the same one.
    """

    event_id: str | None = None
    service_name: str = ""
    timestamp: str | None = None
    method: str = "GET"
    route: str = "/"
    status_code: int = 200
    duration_ms: float = 0.0
    error: bool = False
    error_type: str | None = None
    host: str = _HOSTNAME
    request_bytes: int | None = None
    response_bytes: int | None = None
    consumer_id: str | None = None
    llm_model: str | None = None
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    sdk_version: str = field(default_factory=_get_sdk_version)
    recorded_at: float = field(default_factory=time.time)  # not sent

    def to_dict(self) -> dict:
        if self.event_id is None:
            self.event_id = str(uuid.uuid4())
        if self.timestamp is None:
            self.timestamp = datetime.fromtimestamp(self.recorded_at, timezone.utc).isoformat()
        data = asdict(self)
        del data["recorded_at"]
        return data


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
    request_bytes: int | None = None,
    response_bytes: int | None = None,
    consumer_id: str | None = None,
    llm: tuple[str, int, int] | None = None,
) -> RequestEvent:
    llm_model, llm_input_tokens, llm_output_tokens = llm if llm else (None, None, None)
    return RequestEvent(
        service_name=service_name,
        method=method,
        route=route,
        status_code=status_code,
        duration_ms=duration_ms,
        error=error,
        error_type=error_type,
        sdk_version=sdk_version,
        request_bytes=request_bytes,
        response_bytes=response_bytes,
        consumer_id=consumer_id,
        llm_model=llm_model,
        llm_input_tokens=llm_input_tokens,
        llm_output_tokens=llm_output_tokens,
    )
