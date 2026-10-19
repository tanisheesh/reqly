-- On-call actions on alerts (app/alerts/actions.py). None of them changes
-- what is detected or shown: they only decide which notifications go out.
--
-- acknowledged: someone is on it. No more "still firing" reminders; the
--   resolve notification still goes out.
-- silenced_until: no notifications at all for this alert until then.
-- alert_mutes: no notifications for a service (or one of its routes, or
--   one kind of alert) until muted_until -- planned maintenance, a known
--   flaky dependency. New alerts still open and show on the dashboard.

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS acknowledged_by TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS silenced_until TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS alert_mutes (
    id            BIGSERIAL PRIMARY KEY,
    service_name  TEXT NOT NULL,
    route         TEXT,             -- NULL: every route
    kind          TEXT,             -- NULL: every kind of alert
    muted_until   TIMESTAMPTZ NOT NULL,
    reason        TEXT,
    created_by    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS alert_mutes_active ON alert_mutes (service_name, muted_until);
