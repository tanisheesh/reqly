---
title: Configuration
description: Every environment variable the Reqly collector, dashboard and local stack read.
---

# Configuration

The collector reads its settings from environment variables. Locally, copy `.env.example` to `.env`. `docker compose up` also works without a `.env`: it runs in demo mode, with the demo keys below.

Outside demo mode the collector is locked down by default (collector 0.14+): it won't start without both keys, the dashboard needs a sign-in, and no other origin may call the API.

## Keys and access

| Variable | Default | Notes |
|---|---|---|
| `REQLY_DEMO_MODE` | `false` (`true` in docker-compose) | `true` brings back the public demo defaults: `demo-key` / `demo-read-key`, `PUBLIC_DASHBOARD=true`, `CORS_ORIGINS=*`. For local use and demos only |
| `REQLY_INGEST_KEY` | **required** (demo: `demo-key`) | Write key. SDKs and OTLP exporters send it as `X-Reqly-Key`; it also manages SLOs, specs and projects |
| `REQLY_READ_KEY` | **required** (demo: `demo-read-key`) | Read key the dashboard sends. It's compiled into the dashboard, so treat it as public to anyone who can open the dashboard. **Must differ from the ingest key** |
| `PUBLIC_DASHBOARD` | `false` (demo: `true`) | `true`: anyone with the dashboard can read (a public demo). `false`: [sign-in required](access.md) and the read key is refused |
| `REQLY_ADMIN_USERNAME` / `REQLY_ADMIN_PASSWORD` | `admin` / empty | First admin, created at start-up when there are no users. Password 12+ characters |
| `SESSION_TTL_HOURS` | `168` | How long a sign-in lasts |
| `CORS_ORIGINS` | empty (demo: `*`) | Comma-separated dashboard origins allowed to call the collector. Not needed when the collector serves the dashboard itself ([all-in-one](deploy.md)) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Behind a proxy, the proxy's IP (or `*` if the collector is only reachable through it), so rate limits see real client IPs |
| `RATE_LIMIT_PER_MINUTE` | `600` | Ingest requests (`/v1/ingest`, `/otlp/v1/traces`) per minute **per project key**, so services behind one IP don't share a budget. An SDK sends one request per batch, not per app request |
| `RATE_LIMIT_ENV_KEY_PER_MINUTE` | `6000` | The same for `REQLY_INGEST_KEY`, which every service using it shares. For many services, give each project its own key |
| `RATE_LIMIT_INGEST_PER_IP_PER_MINUTE` | `6000` | Outer ingest cap per client IP, so made-up keys can't get around the per-key limits. Ask Reqly, insights and sign-in stay limited per IP |

The collector refuses to start without the keys (outside demo mode), and warns if they are the demo keys or equal to each other. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

## AI

The weekly report and Ask Reqly use any OpenAI-compatible chat completions API: Groq, OpenAI, OpenRouter, a local Ollama or vLLM. With none configured, the weekly report is plain text and Ask Reqly is off.

| Variable | Default | Notes |
|---|---|---|
| `LLM_BASE_URL` | see notes | The API's base URL, e.g. `https://openrouter.ai/api/v1` or `http://localhost:11434/v1` (Ollama). Unset: OpenAI when `LLM_API_KEY` is set, else Groq when `GROQ_API_KEY` is set |
| `LLM_API_KEY` | empty | The API's key. Local servers like Ollama need none |
| `LLM_MODEL` | `GROQ_MODEL`, else `openai/gpt-oss-120b` | Model for the weekly report, in the provider's naming (`gpt-4.1-mini`, `qwen3:8b`) |
| `GROQ_API_KEY` | empty | Shortcut for Groq ([console.groq.com/keys](https://console.groq.com/keys), free tier). Only sent to Groq, never to `LLM_BASE_URL` |
| `GROQ_MODEL` | empty | Older name for `LLM_MODEL` |
| `ASK_MODEL` | `LLM_MODEL` | Model for Ask Reqly; needs tool calling |
| `ASK_DAILY_LIMIT` | `200` | Ask Reqly questions per collector per day. `0` turns Ask off |
| `LLM_PRICES_FILE` | empty | YAML price table that overrides and extends the built-in one ([LLM cost](../features/llm-cost.md#prices)) |
| `CONSUMER_LIMIT_PER_DAY` | `1000` | Distinct [API consumers](../features/consumers.md#a-cap-on-distinct-consumers) kept per service per day; later new ones are `__other__`. `0` turns the cap off |
| `REQLY_CONSUMER_SALT` | empty | Hashes [consumer ids](../features/consumers.md) that arrive over OpenTelemetry (`enduser.id`). Use the same salt as your SDKs. Empty: OTLP consumer ids are ignored |

## Alerts and jobs

| Variable | Default | Notes |
|---|---|---|
| `ALERTS_ENABLED` | `true` | Hourly anomaly check at :15 |
| `ALERT_SLACK_WEBHOOK_URL` | empty | |
| `ALERT_DISCORD_WEBHOOK_URL` | empty | |
| `ALERT_WEBHOOK_URL` | empty | Generic JSON webhook ([payload](../features/alerts.md#channels)) |
| `ALERT_TEAMS_WEBHOOK_URL` | empty | Microsoft Teams Workflows webhook |
| `ALERT_PAGERDUTY_ROUTING_KEY` | empty | PagerDuty Events API v2 integration key |
| `ALERT_OPSGENIE_API_KEY` / `ALERT_OPSGENIE_API_URL` | empty / `https://api.opsgenie.com` | Opsgenie API integration |
| `ALERT_EMAIL_TO` / `ALERT_EMAIL_FROM` | empty | Email alerts: comma-separated recipients, and the sender |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_STARTTLS` | empty / `587` / empty / empty / `true` | SMTP server for email alerts |
| `RESEND_API_KEY` | empty | Send email alerts through Resend's API instead of SMTP |
| `ALERT_RENOTIFY_HOURS` | `6` | Reminder interval while an alert keeps firing |
| `LLM_COST_ALERT_MIN_USD` | `1.0` | The smallest extra LLM spend per route-hour, in USD, worth an [alert](../features/alerts.md#llm-cost-alerts). `0` turns LLM cost alerts off. The local Compose stack uses `0.4` |
| `DASHBOARD_URL` | `http://localhost:5173` | Linked from Slack alerts |
| `INSIGHTS_SCHEDULER_ENABLED` | `true` | `false` turns off the weekly insights report |
| `LATE_DATA_REFRESH_SECONDS` | `60` | How often late-arriving events are materialized into the aggregates |

## Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | set by Compose | `postgresql://reqly:<password>@<host>:5432/reqly`. Add `?sslmode=require` for a remote database |
| `POSTGRES_PASSWORD` | `localdev` | Local Compose only |

The database must be TimescaleDB with the Toolkit: the `timescale/timescaledb-ha:pg16` image includes both. The collector applies its migrations on start-up.

## Monitoring the collector

| Variable | Default | Notes |
|---|---|---|
| `METRICS_ENABLED` | `true` | Prometheus metrics at `GET /metrics`: requests and latency per collector route, events ingested and rejected per source (SDK, OTLP), database pool connections, and the late-data backlog. No service, project or consumer names appear in them |
| `METRICS_TOKEN` | empty | When set, `/metrics` needs `Authorization: Bearer <token>` (Prometheus: `authorization: {credentials: <token>}`) |
| `LOG_FORMAT` | `text` | `json`: one JSON object per line (`time`, `level`, `logger`, `message`; access lines add `method`, `path`, `status`) for Loki, CloudWatch or Datadog |

`GET /v1/health` answers while the process is up; `GET /v1/ready` also checks the database (503 when it doesn't answer).

## Dashboard

| Variable | Default | Notes |
|---|---|---|
| `DASHBOARD_DIR` | empty (set in the all-in-one image) | The collector serves the built dashboard from this directory on its own origin ([all-in-one](deploy.md#all-in-one-two-containers)) |
| `VITE_COLLECTOR_URL` | `http://localhost:8000` | Collector URL **as seen from the browser**, at build time |
| `VITE_READ_KEY` | `demo-read-key` | At build time |
| `REQLY_COLLECTOR_URL` / `REQLY_READ_KEY` | — | The same two settings at run time, for the prebuilt image |

## Local demo traffic

| Variable | Default | Notes |
|---|---|---|
| `BACKFILL_WEEKS` | `8` | Weeks of synthetic history the load generator creates on first run |
| `BACKFILL_EVENTS_PER_HOUR` | `30` | Synthetic events per hour during the backfill |

SDK settings (`REQLY_SERVICE_NAME`, `REQLY_API_KEY`, …) are listed on the [Python](../instrument/python.md#configuration) and [Node.js](../instrument/node.md#configuration) pages.
