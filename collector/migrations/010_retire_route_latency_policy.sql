-- With the sketch aggregate (api_latency_1min, migration 003) in place, no
-- query reads route_latency_1min any more: stop refreshing it every minute.
-- The view and its history stay (its retention policy still trims it), so
-- nothing that names it breaks. Without the Toolkit this is a no-op and the
-- view keeps serving the percentile_cont queries.

DO $migration$
BEGIN
    IF to_regclass('api_latency_1min') IS NOT NULL THEN
        PERFORM remove_continuous_aggregate_policy('route_latency_1min', if_exists => TRUE);
    END IF;
END
$migration$;
