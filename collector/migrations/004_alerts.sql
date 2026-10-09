-- Hourly anomaly alerts (see app/alerts/).
-- At most one open alert per (service, route): later detections update it
-- instead of opening (and notifying) again; it resolves after two clean
-- hourly checks. last_hour is the start of the most recent anomalous hour.

CREATE TABLE IF NOT EXISTS alerts (
    id                BIGSERIAL PRIMARY KEY,
    service_name      TEXT NOT NULL,
    route             TEXT NOT NULL,
    opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_hour        TIMESTAMPTZ NOT NULL,
    last_hour         TIMESTAMPTZ NOT NULL,
    resolved_at       TIMESTAMPTZ,
    last_notified_at  TIMESTAMPTZ,
    details           JSONB NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS alerts_one_open_per_route
    ON alerts (service_name, route) WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS alerts_by_service_time
    ON alerts (service_name, opened_at DESC);
