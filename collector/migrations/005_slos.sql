-- Service level objectives and error budgets (see app/slo/).
--
-- One objective per row: 'availability' (share of requests without an
-- error) or 'latency' (share of requests at or under latency_threshold_ms).
-- route NULL means the whole service.

CREATE TABLE IF NOT EXISTS slos (
    id                    BIGSERIAL PRIMARY KEY,
    service_name          TEXT NOT NULL,
    name                  TEXT NOT NULL,
    route                 TEXT,
    objective             TEXT NOT NULL CHECK (objective IN ('availability', 'latency')),
    target                DOUBLE PRECISION NOT NULL CHECK (target > 0 AND target < 1),
    latency_threshold_ms  DOUBLE PRECISION CHECK (latency_threshold_ms > 0),
    window_days           INTEGER NOT NULL DEFAULT 28 CHECK (window_days BETWEEN 1 AND 90),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (service_name, name),
    CHECK (objective <> 'latency' OR latency_threshold_ms IS NOT NULL)
);

-- Alerts now come in kinds: hourly 'anomaly' alerts (one open per route)
-- and 'slo' burn-rate alerts (one open per SLO, keyed by route = 'slo:<name>').
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'anomaly';
DROP INDEX IF EXISTS alerts_one_open_per_route;
CREATE UNIQUE INDEX IF NOT EXISTS alerts_one_open_per_route_and_kind
    ON alerts (service_name, route, kind) WHERE resolved_at IS NULL;
