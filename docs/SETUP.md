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
1. **timescaledb** — TimescaleDB pg16; schema applied automatically from `collector/migrations/001_init.sql`
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
| `REQLY_READ_KEY` | same as `REQLY_INGEST_KEY` | Any secret string — sent by the dashboard to read metrics; set separately in production so you can share read access without sharing write credentials |
| `GROQ_API_KEY` | *(empty)* | [console.groq.com/keys](https://console.groq.com/keys) — free tier; leave empty to use plain-text fallback for AI insights |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | See Groq docs for available models |
| `CORS_ORIGINS` | `*` | Comma-separated list of allowed origins; restrict in production |
| `VITE_COLLECTOR_URL` | `http://localhost:8000` | Collector URL as seen from the **browser** (not the Docker network) |
| `BACKFILL_WEEKS` | `8` | Weeks of synthetic history to generate on first load-generator run |
| `BACKFILL_EVENTS_PER_HOUR` | `30` | Synthetic events per hour during backfill |

---

## 3. No database setup required

The TimescaleDB schema (`request_events` hypertable, continuous aggregates, retention policies, `insight_reports` table) is applied automatically when the TimescaleDB container first boots — via `docker-entrypoint-initdb.d`. No manual migration step needed.

If you need to apply the schema manually (e.g., connecting to an external TimescaleDB instance):

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

---

## 6. Trigger AI insights manually

The weekly insights job runs automatically via APScheduler. To trigger it on demand (useful for demos and testing):

```bash
curl -X POST "http://localhost:8000/v1/insights/generate?service_name=your-service" \
  -H "X-Reqly-Key: demo-key"
```

The report is stored and served at:

```bash
curl "http://localhost:8000/v1/insights/latest?service_name=your-service" \
  -H "X-Reqly-Key: demo-key"
```

---

## 7. Deploy to production

See [infra/DEPLOY.md](../infra/DEPLOY.md) for the full AWS production deployment:
- EC2 t3.small running TimescaleDB in Docker (~$15/month)
- Collector as Docker container on EC2 (or Render/Fly.io)
- Dashboard built and deployed to Vercel / S3+CloudFront / Render
- AWS SAM stack for Lambda weekly insights + EventBridge + S3 archive

---

## Known local-only limitations

- The load generator is a Docker service, not a real app — it generates synthetic traffic patterns. To see AI insights from real traffic, instrument your own app or run the [EventFlow demo](../demo/README.md).
- `GROQ_API_KEY` is required for AI-written insights. Without it the insights panel shows plain-text statistical findings — fully functional, just not LLM-narrated.
- The continuous aggregate `end_offset` is 1 minute, so the most recent ~1 minute of data may not appear in dashboard queries (it's in the raw table but not yet in the aggregate). This is expected TimescaleDB behavior.
