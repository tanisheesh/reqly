-- Projects, which service belongs to which, per-project API keys and
-- project members (see app/projects/).
--
-- Every service belongs to exactly one project. Services that already have
-- data go to the "default" project; a new service joins the project of the
-- key that sends its first events. The env keys (REQLY_INGEST_KEY,
-- REQLY_READ_KEY) and admin users keep access to every project.

CREATE TABLE IF NOT EXISTS projects (
    id          BIGSERIAL PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO projects (slug, name) VALUES ('default', 'Default') ON CONFLICT (slug) DO NOTHING;

CREATE TABLE IF NOT EXISTS project_services (
    service_name  TEXT PRIMARY KEY,
    project_id    BIGINT NOT NULL REFERENCES projects (id) ON DELETE RESTRICT,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO project_services (service_name, project_id)
SELECT DISTINCT service_name, (SELECT id FROM projects WHERE slug = 'default')
FROM request_events
ON CONFLICT (service_name) DO NOTHING;

INSERT INTO project_services (service_name, project_id)
SELECT DISTINCT service_name, (SELECT id FROM projects WHERE slug = 'default')
FROM route_latency_1min
ON CONFLICT (service_name) DO NOTHING;

-- Keys are random (rqk_ + 256 bits) and stored as SHA-256; `prefix` is the
-- start of the key, shown so people can tell keys apart.
CREATE TABLE IF NOT EXISTS api_keys (
    id            BIGSERIAL PRIMARY KEY,
    project_id    BIGINT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    prefix        TEXT NOT NULL,
    key_hash      TEXT NOT NULL UNIQUE,
    scopes        TEXT[] NOT NULL CHECK (scopes <@ ARRAY['ingest', 'read', 'admin'] AND cardinality(scopes) > 0),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by    BIGINT REFERENCES users (id) ON DELETE SET NULL,
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS api_keys_project ON api_keys (project_id);

CREATE TABLE IF NOT EXISTS project_members (
    project_id  BIGINT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id)
);
