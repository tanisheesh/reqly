-- Requests that call several LLM models (ingest spec: the event's llm_usage).
--
-- request_events keeps what it always stored for such a request: the model
-- with the most tokens and the tokens of all of them. That is what SDKs
-- before 0.6 / 0.4 send, and what llm_usage_1hour (90 days of history) is
-- built from. Priced as one model, though, the other models' tokens get the
-- main model's rate.
--
-- So for each such request this table holds one row per model with the
-- tokens to move: the other models' tokens subtracted from the main model,
-- and each other model's own tokens added. One request's rows sum to zero,
-- so token totals and request counts don't change; adding
-- llm_usage_splits_1hour to llm_usage_1hour gives every model its own
-- tokens, and its own price. Requests with one model have no rows here.

CREATE TABLE IF NOT EXISTS llm_usage_splits (
    time          TIMESTAMPTZ NOT NULL,
    event_id      UUID NOT NULL,
    service_name  TEXT NOT NULL,
    route         TEXT NOT NULL,
    llm_model     TEXT NOT NULL,
    input_tokens  BIGINT NOT NULL,
    output_tokens BIGINT NOT NULL,
    -- the event's own dedup key, per model: a retried batch adds nothing
    PRIMARY KEY (time, event_id, llm_model)
);

SELECT create_hypertable(
    'llm_usage_splits', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);
SELECT add_retention_policy('llm_usage_splits', INTERVAL '14 days', if_not_exists => TRUE);

CREATE MATERIALIZED VIEW IF NOT EXISTS llm_usage_splits_1hour
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    service_name,
    route,
    llm_model,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens
FROM llm_usage_splits
GROUP BY bucket, service_name, route, llm_model
WITH NO DATA;

-- The same schedule and retention as llm_usage_1hour, so the two always
-- cover the same hours.
SELECT add_continuous_aggregate_policy('llm_usage_splits_1hour',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);
SELECT add_retention_policy('llm_usage_splits_1hour', INTERVAL '90 days', if_not_exists => TRUE);
