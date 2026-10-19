-- One row per scheduled job run (app/insights/scheduler.py). A collector
-- runs a job only if its INSERT of (job, slot) lands, so two collectors on
-- one database -- a rolling deploy, a second replica by mistake -- never
-- both send the hourly alerts or write (and pay for) the weekly AI report.
-- A claim, not a lock: it holds whatever the two schedulers' timing.
CREATE TABLE IF NOT EXISTS scheduled_runs (
    job         TEXT NOT NULL,
    slot        TIMESTAMPTZ NOT NULL,  -- the hour, 5 minutes or week the run covers
    claimed_by  TEXT NOT NULL,
    claimed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job, slot)
);
