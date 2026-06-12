from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import settings

# In-memory single-instance limiter — enough to demonstrate the concept
# without needing Redis-backed distributed rate limiting. Explicitly does
# NOT scale horizontally as-is; that's a documented tradeoff, not an
# oversight (see ARCHITECTURE.md §3).
limiter = Limiter(key_func=get_remote_address, default_limits=[])

RATE_LIMIT = f"{settings.rate_limit_per_minute}/minute"
