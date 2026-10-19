-- Trace correlation: the trace id of a request that failed (4xx/5xx) or was
-- slow, so an alert can point at a few traces to open in Tempo, Jaeger or
-- Sentry. Other requests don't keep it (TRACE_ID_SLOW_MS, app/traces.py):
-- nobody looks up the trace of a fast 200.
ALTER TABLE request_events ADD COLUMN IF NOT EXISTS trace_id TEXT;
