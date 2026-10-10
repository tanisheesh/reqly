"""Consumer cardinality cap.

consumer_id comes from the app (a header, or the app's own function), so a
misconfigured SDK -- a request id or a timestamp passed as the consumer --
would create a new "consumer" per request and bloat consumer_usage_1hour.

Per service and UTC day, the first CONSUMER_LIMIT_PER_DAY distinct consumers
are kept as they are; later new ones are recorded as "__other__". Consumers
already seen that day keep their id. It's a soft cap: two batches ingested at
the same moment can each admit the last free slot, and like the rate limits
the state is per collector process (a restart reloads it from the database).
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone

import asyncpg

OTHER = "__other__"
_MAX_DAYS_CACHED = 256  # (service, day) entries kept in memory

_DISTINCT_SQL = """
SELECT DISTINCT consumer_id FROM request_events
WHERE service_name = $1 AND time >= $2 AND time < $3
  AND consumer_id IS NOT NULL AND consumer_id <> $4
LIMIT $5
"""


class ConsumerCap:
    def __init__(self) -> None:
        self._seen: OrderedDict[tuple[str, date], set[str]] = OrderedDict()

    async def _known(self, pool: asyncpg.Pool, service: str, day: date, limit: int) -> set[str]:
        key = (service, day)
        if key in self._seen:
            self._seen.move_to_end(key)
            return self._seen[key]
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        rows = await pool.fetch(_DISTINCT_SQL, service, start, start + timedelta(days=1), OTHER, limit)
        known = {r["consumer_id"] for r in rows}
        self._seen[key] = known
        while len(self._seen) > _MAX_DAYS_CACHED:
            self._seen.popitem(last=False)
        return known

    async def apply(
        self, pool: asyncpg.Pool, service: str, events: list[tuple[datetime, str | None]], limit: int
    ) -> list[str | None]:
        """The consumer id to store for each (timestamp, consumer_id), in order."""
        if limit <= 0:
            return [consumer for _, consumer in events]
        out = []
        for timestamp, consumer in events:
            if consumer is None or consumer == OTHER:
                out.append(consumer)
                continue
            known = await self._known(pool, service, timestamp.astimezone(timezone.utc).date(), limit)
            if consumer in known:
                out.append(consumer)
            elif len(known) < limit:
                known.add(consumer)
                out.append(consumer)
            else:
                out.append(OTHER)
        return out

    def clear(self) -> None:
        self._seen.clear()


consumer_cap = ConsumerCap()
