from __future__ import annotations

import hashlib
import hmac

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from .config import settings

# In-memory, single-instance limiter -- enough to demonstrate the concept
# without needing Redis-backed distributed rate limiting. Explicitly does
# NOT scale horizontally as-is; that's a documented tradeoff, not an
# oversight (see ARCHITECTURE.md §3).
#
# The default key is the client IP: right for Ask Reqly, insights and login,
# whose callers share one public read key (limiting by key there would let
# one visitor use up everyone's budget).
limiter = Limiter(key_func=get_remote_address, default_limits=[])

RATE_LIMIT = f"{settings.rate_limit_per_minute}/minute"

# --- ingest: per API key ---------------------------------------------------------
# Services behind one NAT or in one cluster share an IP, so ingest is limited
# per key. Each project key gets RATE_LIMIT; the env ingest key, shared by every
# service that uses it, gets RATE_LIMIT_ENV_KEY_PER_MINUTE. A per-IP cap on top
# keeps a client from dodging the limit by sending made-up keys.
ENV_KEY_BUCKET = "ingest:env-key"
ENV_KEY_LIMIT = f"{settings.rate_limit_env_key_per_minute}/minute"
INGEST_IP_LIMIT = f"{settings.rate_limit_ingest_per_ip_per_minute}/minute"


def ingest_key(request: Request) -> str:
    key = request.headers.get("x-reqly-key") or ""
    if not key:
        return "ingest:ip:" + get_remote_address(request)
    if hmac.compare_digest(key.encode(), settings.ingest_key.encode()):
        return ENV_KEY_BUCKET
    # hashed: the limiter's storage never holds a key
    return "ingest:key:" + hashlib.sha256(key.encode()).hexdigest()[:24]


def ingest_limit(key: str) -> str:
    return ENV_KEY_LIMIT if key == ENV_KEY_BUCKET else RATE_LIMIT
