#!/bin/bash
# Amazon Linux 2023 bootstrap — installs Docker + TimescaleDB + full Reqly schema
# Usage: pass as --user-data when launching an EC2 instance (see infra/DEPLOY.md)
# Replace POSTGRES_PASSWORD with your own secure password before use.
set -e

POSTGRES_USER=reqly
POSTGRES_DB=reqly
POSTGRES_PASSWORD=your_password_here   # <-- change this

LOGFILE=/var/log/reqly-setup.log
exec > >(tee -a "$LOGFILE") 2>&1

echo "=== Reqly TimescaleDB Setup ==="
date

# Install Docker
amazon-linux-extras install docker -y 2>/dev/null || dnf install -y docker
systemctl start docker
systemctl enable docker
usermod -aG docker ec2-user
echo "Docker started"

# Create data dir
mkdir -p /data/postgres

# Pull + run TimescaleDB
docker pull timescale/timescaledb:latest-pg16

docker run -d \
  --name timescaledb \
  -p 5432:5432 \
  -e POSTGRES_DB=${POSTGRES_DB} \
  -e POSTGRES_USER=${POSTGRES_USER} \
  -e POSTGRES_PASSWORD=${POSTGRES_PASSWORD} \
  -v /data/postgres:/var/lib/postgresql/data \
  --restart unless-stopped \
  timescale/timescaledb:latest-pg16

echo "Container started, waiting for DB to be ready..."

for i in $(seq 1 30); do
  if docker exec timescaledb pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB} 2>/dev/null; then
    echo "DB ready after $((i*5))s"
    break
  fi
  sleep 5
done

# Apply schema
cat > /root/schema.sql << 'SQLEOF'
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS request_events (
    event_id        UUID NOT NULL,
    time            TIMESTAMPTZ NOT NULL,
    service_name    TEXT NOT NULL,
    method          TEXT NOT NULL,
    route           TEXT NOT NULL,
    status_code     SMALLINT NOT NULL,
    duration_ms     DOUBLE PRECISION NOT NULL,
    is_error        BOOLEAN NOT NULL,
    error_type      TEXT,
    host            TEXT,
    PRIMARY KEY (time, event_id)
);

SELECT create_hypertable('request_events', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_re_service_route_time
    ON request_events (service_name, route, time DESC);
CREATE INDEX IF NOT EXISTS idx_re_errors
    ON request_events (service_name, time DESC) WHERE is_error;

CREATE MATERIALIZED VIEW IF NOT EXISTS route_latency_1min
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', time) AS bucket, service_name, route,
    count(*) AS request_count,
    percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms) AS p99_ms,
    avg(duration_ms) AS avg_ms
FROM request_events GROUP BY bucket, service_name, route
WITH NO DATA;

SELECT add_continuous_aggregate_policy('route_latency_1min',
    start_offset => INTERVAL '1 hour', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE);

CREATE MATERIALIZED VIEW IF NOT EXISTS route_errors_1hour
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', time) AS bucket, service_name, route,
    count(*) AS request_count,
    count(*) FILTER (WHERE is_error) AS error_count,
    (count(*) FILTER (WHERE is_error))::float / count(*) AS error_rate,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
FROM request_events GROUP BY bucket, service_name, route
WITH NO DATA;

SELECT add_continuous_aggregate_policy('route_errors_1hour',
    start_offset => INTERVAL '3 hours', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE);

CREATE MATERIALIZED VIEW IF NOT EXISTS route_status_distribution_1hour
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', time) AS bucket, service_name, route,
    status_code, count(*) AS count
FROM request_events GROUP BY bucket, service_name, route, status_code
WITH NO DATA;

SELECT add_continuous_aggregate_policy('route_status_distribution_1hour',
    start_offset => INTERVAL '3 hours', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE);

SELECT add_retention_policy('request_events',                  INTERVAL '14 days',  if_not_exists => TRUE);
SELECT add_retention_policy('route_latency_1min',              INTERVAL '90 days',  if_not_exists => TRUE);
SELECT add_retention_policy('route_errors_1hour',              INTERVAL '180 days', if_not_exists => TRUE);
SELECT add_retention_policy('route_status_distribution_1hour', INTERVAL '180 days', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS insight_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_name    TEXT NOT NULL,
    week_start      DATE NOT NULL,
    anomalies_json  JSONB NOT NULL,
    report_text     TEXT NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (service_name, week_start)
);
SQLEOF

docker exec -i timescaledb psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} < /root/schema.sql
echo "=== Schema applied ==="
date
echo "=== Reqly setup complete ==="
