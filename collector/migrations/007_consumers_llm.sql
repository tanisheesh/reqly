-- Hourly rollups for consumer analytics and LLM cost (see app/consumers/ and
-- app/llm/). Raw events cover 14 days; these keep 90.
--
-- Neither view filters out events without a consumer / LLM model: the NULL
-- rows double as per-route request totals (cost per 1k requests, share of
-- traffic with a consumer id), and a view that stays empty would be
-- re-seeded from the raw table on every collector start.
--
-- consumer_id is kept out of the minute-level aggregates on purpose: its
-- cardinality is unbounded.

CREATE MATERIALIZED VIEW IF NOT EXISTS consumer_usage_1hour
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    service_name,
    consumer_id,
    route,
    method,
    count(*) AS request_count,
    count(*) FILTER (WHERE is_error) AS error_count
FROM request_events
GROUP BY bucket, service_name, consumer_id, route, method
WITH NO DATA;

SELECT add_continuous_aggregate_policy('consumer_usage_1hour',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);
SELECT add_retention_policy('consumer_usage_1hour', INTERVAL '90 days', if_not_exists => TRUE);

CREATE MATERIALIZED VIEW IF NOT EXISTS llm_usage_1hour
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    service_name,
    route,
    llm_model,
    count(*) AS request_count,
    coalesce(sum(llm_input_tokens), 0) AS input_tokens,
    coalesce(sum(llm_output_tokens), 0) AS output_tokens
FROM request_events
GROUP BY bucket, service_name, route, llm_model
WITH NO DATA;

SELECT add_continuous_aggregate_policy('llm_usage_1hour',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);
SELECT add_retention_policy('llm_usage_1hour', INTERVAL '90 days', if_not_exists => TRUE);
