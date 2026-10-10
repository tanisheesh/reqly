# Local Setup — Reqly

> **Just want to try it?** Use the live demo at [reqly-eventflow-dashboard.onrender.com](https://reqly-eventflow-dashboard.onrender.com) — no setup needed.
> **Using Reqly in your app?** The [documentation site](https://reqly.tanisheesh.in/docs/) has the SDK guides, every feature and the HTTP API.
> This guide is for running the repository locally to develop Reqly.

---

## Prerequisites

- Docker + Docker Compose (tested with Compose v2.x)
- Python 3.9+ (only needed if you want to run the SDK or collector outside Docker)
- Node.js 20+ (only needed to develop the dashboard or the Node SDK outside Docker)

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
| `PUBLIC_DASHBOARD` | `true` | `true`: anyone with the dashboard can read (public demo). `false`: sign-in required |
| `REQLY_ADMIN_USERNAME` / `REQLY_ADMIN_PASSWORD` | `admin` / *(empty)* | First admin, created at start-up when there are no users (password 12+ characters) |
| `SESSION_TTL_HOURS` | `168` | How long a sign-in lasts |
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

## 5. Use it

With the stack running, everything else is on the documentation site:

| To | See |
|---|---|
| Instrument a Python, Node.js or OpenTelemetry app | [Python](https://reqly.tanisheesh.in/docs/instrument/python/) · [Node.js](https://reqly.tanisheesh.in/docs/instrument/node/) · [OpenTelemetry](https://reqly.tanisheesh.in/docs/instrument/opentelemetry/) |
| Trigger the weekly report on demand | [Alerts & weekly report](https://reqly.tanisheesh.in/docs/features/alerts/#weekly-report) |
| Define SLOs | [SLOs & error budgets](https://reqly.tanisheesh.in/docs/features/slos/) |
| Upload an OpenAPI spec | [OpenAPI drift](https://reqly.tanisheesh.in/docs/features/openapi-drift/) |
| Set up projects, keys and sign-in | [Projects, keys & sign-in](https://reqly.tanisheesh.in/docs/self-hosting/access/) |
| Call the API | [HTTP API](https://reqly.tanisheesh.in/docs/reference/http-api/), or Swagger UI at `http://localhost:8000/docs` |

The local stack accepts the ingest key `demo-key` and the read key `demo-read-key`. The demo creates SLOs and uploads an OpenAPI spec for both demo services on start-up.

---

## 7. Deploy to production

See [DEPLOY.md](DEPLOY.md) for how the live demo runs (Render, EC2, Vercel), production env vars, the prebuilt images, releases, rollback and monitoring.

---

## Upgrading an existing local stack

The database image moved to `timescale/timescaledb-ha:pg16`, which runs as a different user and keeps its data in a different directory, so it uses a new volume (`timescale_ha_data`). After pulling, `docker compose up -d` starts with an empty database and the load generator backfills it again. The old volume is no longer used; remove it with `docker volume rm reqly_timescale_data` once you don't need it.

## Known local-only limitations

- The load generator is a Docker service, not a real app — it generates synthetic traffic patterns. To see AI insights from real traffic, instrument your own app or run the [EventFlow demo](../demo/README.md).
- `GROQ_API_KEY` is required for AI-written insights. Without it the insights panel shows plain-text statistical findings — fully functional, just not LLM-narrated.
- The continuous aggregate `end_offset` is 1 minute, so the most recent ~1 minute of data may not appear in dashboard queries (it's in the raw table but not yet in the aggregate). This is expected TimescaleDB behavior.
- On first start, the load generator backfills weeks of history. The collector materializes it into the aggregates in the background (about a minute per few weeks of demo data), so the 7d chart and AI insights fill in shortly after the backfill finishes. Re-running the backfill doesn't duplicate data — events are deterministic and deduplicated.

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
