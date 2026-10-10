# Local Setup — Reqly

> **Just want to try it?** Use the live demo at [reqly-eventflow-dashboard.onrender.com](https://reqly-eventflow-dashboard.onrender.com) — no setup needed.
> This guide is for running Reqly locally or self-hosting it.

---

## Prerequisites

- Docker + Docker Compose (tested with Compose v2.x)
- Python 3.9+ (only needed if you want to run the SDK or collector outside Docker)
- Node.js 20+ (only needed if you want to develop the dashboard outside Docker)

---

## 1. Clone and install

```bash
git clone https://github.com/tanisheesh/reqly
cd reqly
docker compose up -d
```

That's it for the full local stack. Docker Compose starts:
1. **timescaledb** — `timescale/timescaledb-ha:pg16` (TimescaleDB + Toolkit; the collector applies `collector/migrations/` on startup)
2. **collector** — FastAPI ingest + metrics API on `http://localhost:8000`
3. **dashboard** — React SPA on `http://localhost:5173`
4. **load-generator** — backfills 8 weeks of synthetic history then generates ~2 RPS of live traffic

The load generator runs automatically so the dashboard has data immediately. Open `http://localhost:5173`, select a service, and you'll see live metrics.

---

## 2. Environment variables

Copy `.env.example` to `.env` and fill in any values you want to override. The defaults work out of the box for local development — `docker compose up` works without a `.env` at all.

```bash
cp .env.example .env
```

| Variable | Default | Where to get it |
|---|---|---|
| `POSTGRES_PASSWORD` | `localdev` | Any value — used internally by Docker Compose |
| `REQLY_INGEST_KEY` | `demo-key` | Any secret string — sent by the SDK as `X-Reqly-Key` on ingest |
| `REQLY_READ_KEY` | `demo-read-key` | Sent by the dashboard to read metrics. It is compiled into the dashboard bundle, so treat it as public to dashboard viewers — it must differ from `REQLY_INGEST_KEY` |
| `GROQ_API_KEY` | *(empty)* | [console.groq.com/keys](https://console.groq.com/keys) — free tier; leave empty to use plain-text fallback for AI insights |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | See Groq docs for available models |
| `ASK_MODEL` | `GROQ_MODEL` | Model for Ask Reqly; needs tool calling |
| `LLM_PRICES_FILE` | *(empty)* | YAML price table that overrides/extends `collector/app/llm/llm_prices.yaml` (USD per 1M tokens) |
| `ASK_DAILY_LIMIT` | `200` | Ask Reqly questions per day per collector (the read key is public to dashboard viewers); `0` turns Ask off |
| `CORS_ORIGINS` | `*` | Comma-separated list of allowed origins; restrict in production |
| `INSIGHTS_SCHEDULER_ENABLED` | `true` | Set `false` when the SAM Lambda runs the weekly job, so it doesn't run twice |
| `ALERTS_ENABLED` | `true` | Hourly anomaly check (open alerts appear on the dashboard) |
| `ALERT_SLACK_WEBHOOK_URL` / `ALERT_DISCORD_WEBHOOK_URL` / `ALERT_WEBHOOK_URL` | *(empty)* | Where alert notifications go; unset channels are skipped |
| `ALERT_RENOTIFY_HOURS` | `6` | Reminder interval while an alert keeps firing |
| `DASHBOARD_URL` | `http://localhost:5173` | Linked from Slack alerts |
| `LATE_DATA_REFRESH_SECONDS` | `60` | How often late-arriving events (older than 1h) are materialized into the aggregates |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Behind a proxy, the proxy's IP — lets rate limiting see the real client IP |
| `VITE_COLLECTOR_URL` | `http://localhost:8000` | Collector URL as seen from the **browser** (not the Docker network) |
| `BACKFILL_WEEKS` | `8` | Weeks of synthetic history to generate on first load-generator run |
| `BACKFILL_EVENTS_PER_HOUR` | `30` | Synthetic events per hour during backfill |

---

## 3. No database setup required

The TimescaleDB schema (`request_events` hypertable, continuous aggregates, retention policies, `insight_reports` table) is applied automatically by the collector on startup — it runs every file in `collector/migrations/` once and records it in a `schema_migrations` table. This works the same against an external TimescaleDB instance. No manual migration step needed.

If you want to apply the schema by hand anyway:

```bash
psql postgresql://reqly:your_password@your-host:5432/reqly \
  -f collector/migrations/001_init.sql
```

---

## 4. Run locally

```bash
# Full stack (timescaledb + collector + dashboard + load-generator)
docker compose up -d

# Check everything is healthy
docker compose ps

# Collector logs
docker compose logs collector -f

# Tail load generator to see synthetic traffic
docker compose logs load-generator -f
```

- Dashboard: `http://localhost:5173`
- Collector API docs (Swagger UI): `http://localhost:8000/docs`
- Collector health: `http://localhost:8000/v1/health`

---

## 5. Instrument your own app

Install the SDK:

```bash
pip install reqly
```

**FastAPI:**

```python
import reqly
from fastapi import FastAPI

app = FastAPI()
reqly.instrument(
    app,
    service_name="my-api",
    collector_url="http://localhost:8000",  # default
    api_key="demo-key",                     # matches REQLY_INGEST_KEY
)
```

**Flask:**

```python
import reqly
from flask import Flask

app = Flask(__name__)
reqly.instrument(
    app,
    service_name="my-api",
    collector_url="http://localhost:8000",
    api_key="demo-key",
)
```

All options can also be set via environment variables (resolution order: kwarg → env var → default):

| kwarg | env var | default |
|---|---|---|
| `service_name` | `REQLY_SERVICE_NAME` | `sys.argv[0]` basename |
| `collector_url` | `REQLY_COLLECTOR_URL` | `http://localhost:8000` |
| `api_key` | `REQLY_API_KEY` | `None` |
| `sample_rate` | `REQLY_SAMPLE_RATE` | `1.0` |
| `flush_interval_seconds` | `REQLY_FLUSH_INTERVAL_SECONDS` | `5.0` |
| `max_batch_size` | `REQLY_MAX_BATCH_SIZE` | `200` |
| `max_queue_size` | `REQLY_MAX_QUEUE_SIZE` | `2000` |
| `ignore_routes` | `REQLY_IGNORE_ROUTES` | `/health,/metrics` |
| `release` | `REQLY_RELEASE` (then CI vars like `GITHUB_SHA`, `RENDER_GIT_COMMIT`) | auto-detected or `None` |
| `environment` | `REQLY_ENVIRONMENT` | `None` |

---

## 6. Trigger AI insights manually

The weekly insights job runs automatically via APScheduler. To trigger it on demand (useful for demos and testing):

```bash
curl -X POST "http://localhost:8000/v1/insights/generate?service_name=your-service" \
  -H "X-Reqly-Key: demo-read-key"
```

The report is stored and served at:

```bash
curl "http://localhost:8000/v1/insights/latest?service_name=your-service" \
  -H "X-Reqly-Key: demo-read-key"
```

---

## 6b. Define SLOs

SLOs are managed with the ingest key (the read key can't change anything):

```bash
curl -X PUT http://localhost:8000/v1/slos \
  -H "X-Reqly-Key: demo-key" -H "Content-Type: application/json" \
  -d '{"service_name": "checkout-api", "name": "checkout availability",
       "route": "/checkout", "objective": "availability", "target": 0.995}'

curl -X PUT http://localhost:8000/v1/slos \
  -H "X-Reqly-Key: demo-key" -H "Content-Type: application/json" \
  -d '{"service_name": "checkout-api", "name": "API latency",
       "objective": "latency", "target": 0.95, "latency_threshold_ms": 800}'
```

Leave out `route` for a service-wide SLO; `window_days` defaults to 28. The dashboard shows each SLO's error budget, and burn-rate alerts go to the alert channels. The local demo creates three SLOs for the demo services on startup.

## 6c. Compare traffic with your OpenAPI spec

FastAPI and Litestar apps can upload their own spec: `reqly.instrument(app, push_openapi=True)`
(or `REQLY_PUSH_OPENAPI=true`). For anything else, upload it from CI, JSON or YAML:

```bash
curl -X PUT http://localhost:8000/v1/services/checkout-api/openapi \
  -H "X-Reqly-Key: demo-key" -H "Content-Type: application/yaml" \
  --data-binary @openapi.yaml
```

Add `?base_path=/api` if the app serves the spec's paths under a prefix (Swagger 2.0's
`basePath` is used automatically). The dashboard then shows an **API surface** panel:
undocumented endpoints that get traffic, documented ones nobody called in 30 days, and
deprecated ones still in use. The local demo uploads a spec for both demo services.

## 7. Deploy to production

See [infra/DEPLOY.md](../infra/DEPLOY.md) for the full AWS production deployment:
- EC2 t3.small running TimescaleDB in Docker (~$15/month)
- Collector as Docker container on EC2 (or Render/Fly.io)
- Dashboard built and deployed to Vercel / S3+CloudFront / Render
- AWS SAM stack for Lambda weekly insights + EventBridge + S3 archive

---

## Upgrading an existing local stack

The database image moved to `timescale/timescaledb-ha:pg16`, which runs as a different user and keeps its data in a different directory, so it uses a new volume (`timescale_ha_data`). After pulling, `docker compose up -d` starts with an empty database and the load generator backfills it again. The old volume is no longer used; remove it with `docker volume rm reqly_timescale_data` once you don't need it.

## Known local-only limitations

- The load generator is a Docker service, not a real app — it generates synthetic traffic patterns. To see AI insights from real traffic, instrument your own app or run the [EventFlow demo](../demo/README.md).
- `GROQ_API_KEY` is required for AI-written insights. Without it the insights panel shows plain-text statistical findings — fully functional, just not LLM-narrated.
- The continuous aggregate `end_offset` is 1 minute, so the most recent ~1 minute of data may not appear in dashboard queries (it's in the raw table but not yet in the aggregate). This is expected TimescaleDB behavior.
- On first start, the load generator backfills weeks of history. The collector materializes it into the aggregates in the background (about a minute per few weeks of demo data), so the 7d chart and AI insights fill in shortly after the backfill finishes. Re-running the backfill doesn't duplicate data — events are deterministic and deduplicated.
