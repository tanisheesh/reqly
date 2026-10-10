-- The OpenAPI spec of each service, compared with observed traffic to find
-- drift (see app/openapi/). One spec per service; uploading replaces it.
-- base_path is prepended to the spec's paths when matching, for APIs whose
-- spec paths are relative to a prefix the app serves them under.

CREATE TABLE IF NOT EXISTS api_specs (
    service_name  TEXT PRIMARY KEY,
    spec          JSONB NOT NULL,
    base_path     TEXT NOT NULL DEFAULT '',
    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
