-- Mergeable latency percentiles (TimescaleDB Toolkit).
--
-- percentile_cont results can't be combined: the service-level p95 used to be
-- "the max of the route p95s" and multi-hour windows "the max of the minute
-- p95s" -- upper bounds, not percentiles. A UddSketch per (minute, route, ...)
-- can be rolled up across routes and time into a real percentile.
--
-- uddsketch(1000, 0.005): within ~0.2% of the exact p50/p95/p99 on log-normal
-- API latencies (1ms-5min range never collapses 1000 buckets), ~130 bytes per
-- route-minute.
--
-- The Toolkit ships with timescale/timescaledb-ha and Timescale Cloud but not
-- with the plain timescale/timescaledb image. Without it this migration is a
-- no-op and the collector keeps using the percentile_cont views (it checks for
-- api_latency_1min at runtime). To enable it later: install the Toolkit, then
-- DELETE FROM schema_migrations WHERE filename = '003_latency_sketches.sql'
-- and restart the collector.

DO $migration$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb_toolkit') THEN
        RAISE NOTICE 'timescaledb_toolkit not available; skipping api_latency_1min';
        RETURN;
    END IF;

    CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;

    -- Dynamic SQL so the Toolkit's functions are resolved after the
    -- extension exists. environment and method are in the key now so later
    -- filters don't need another aggregate.
    EXECUTE $view$
        CREATE MATERIALIZED VIEW IF NOT EXISTS api_latency_1min
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 minute', time) AS bucket,
            service_name,
            environment,
            route,
            method,
            count(*) AS request_count,
            count(*) FILTER (WHERE is_error) AS error_count,
            uddsketch(1000, 0.005, duration_ms) AS latency
        FROM request_events
        GROUP BY bucket, service_name, environment, route, method
        WITH NO DATA
    $view$;

    PERFORM add_continuous_aggregate_policy('api_latency_1min',
        start_offset => INTERVAL '1 hour',
        end_offset => INTERVAL '1 minute',
        schedule_interval => INTERVAL '1 minute',
        if_not_exists => TRUE
    );
    PERFORM add_retention_policy('api_latency_1min', INTERVAL '90 days', if_not_exists => TRUE);
END
$migration$;
