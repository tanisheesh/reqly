-- Event schema v2 (see docs/INGEST_SPEC.md).
-- All new columns are nullable so older SDKs keep working unchanged. Adding
-- nullable columns to a hypertable is a metadata-only change; the existing
-- continuous aggregates are unaffected.

ALTER TABLE request_events
    ADD COLUMN IF NOT EXISTS release            TEXT,
    ADD COLUMN IF NOT EXISTS environment        TEXT,
    ADD COLUMN IF NOT EXISTS consumer_id        TEXT,
    ADD COLUMN IF NOT EXISTS request_bytes      BIGINT,
    ADD COLUMN IF NOT EXISTS response_bytes     BIGINT,
    ADD COLUMN IF NOT EXISTS llm_model          TEXT,
    ADD COLUMN IF NOT EXISTS llm_input_tokens   INTEGER,
    ADD COLUMN IF NOT EXISTS llm_output_tokens  INTEGER;

-- One row per release ever seen, per service and environment. Written by
-- ingest; read by deploy-aware anomaly detection to answer "which release
-- was running when this regression started?". environment is '' (not
-- NULL) when the SDK doesn't send one, so it can be part of the key.
CREATE TABLE IF NOT EXISTS deployments (
    service_name   TEXT NOT NULL,
    environment    TEXT NOT NULL DEFAULT '',
    release        TEXT NOT NULL,
    first_seen_at  TIMESTAMPTZ NOT NULL,
    last_seen_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (service_name, environment, release)
);

CREATE INDEX IF NOT EXISTS idx_deployments_first_seen
    ON deployments (service_name, first_seen_at DESC);
