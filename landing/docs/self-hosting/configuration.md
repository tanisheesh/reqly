---
title: Configuration
description: Every environment variable the Reqly collector, dashboard and local stack read.
---

# Configuration

The collector reads its settings from environment variables. Locally, copy `.env.example` to `.env`. `docker compose up` also works without a `.env`, using the defaults below.

## Keys and access

| Variable | Default | Notes |
|---|---|---|
| `REQLY_INGEST_KEY` | `demo-key` | Write key. SDKs and OTLP exporters send it as `X-Reqly-Key`; it also manages SLOs, specs and projects |
| `REQLY_READ_KEY` | `demo-read-key` | Read key the dashboard sends. It's compiled into the dashboard, so treat it as public to anyone who can open the dashboard. **Must differ from the ingest key** |
| `PUBLIC_DASHBOARD` | `true` | `true`: anyone with the dashboard can read (a public demo). `false`: [sign-in required](access.md) and the read key is refused |
| `REQLY_ADMIN_USERNAME` / `REQLY_ADMIN_PASSWORD` | `admin` / empty | First admin, created at start-up when there are no users. Password 12+ characters |
| `SESSION_TTL_HOURS` | `168` | How long a sign-in lasts |
| `CORS_ORIGINS` | `*` locally | Comma-separated dashboard origins allowed to call the collector. Restrict it in production |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Behind a proxy, the proxy's IP (or `*` if the collector is only reachable through it), so rate limits see real client IPs |

The collector warns at start-up if the keys are the defaults or if the two keys are equal.

## AI

| Variable | Default | Notes |
|---|---|---|
| `GROQ_API_KEY` | empty | [console.groq.com/keys](https://console.groq.com/keys), free tier. Empty: plain-text weekly report, Ask Reqly off |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Model for the weekly report |
| `ASK_MODEL` | `GROQ_MODEL` | Model for Ask Reqly; needs tool calling |
| `ASK_DAILY_LIMIT` | `200` | Ask Reqly questions per collector per day. `0` turns Ask off |
| `LLM_PRICES_FILE` | empty | YAML price table that overrides and extends the built-in one ([LLM cost](../features/llm-cost.md#prices)) |

## Alerts and jobs

| Variable | Default | Notes |
|---|---|---|
| `ALERTS_ENABLED` | `true` | Hourly anomaly check at :15 |
| `ALERT_SLACK_WEBHOOK_URL` | empty | |
| `ALERT_DISCORD_WEBHOOK_URL` | empty | |
| `ALERT_WEBHOOK_URL` | empty | Generic JSON webhook ([payload](../features/alerts.md#channels)) |
| `ALERT_RENOTIFY_HOURS` | `6` | Reminder interval while an alert keeps firing |
| `DASHBOARD_URL` | `http://localhost:5173` | Linked from Slack alerts |
| `INSIGHTS_SCHEDULER_ENABLED` | `true` | `false` when the AWS Lambda runs the weekly report instead |
| `LATE_DATA_REFRESH_SECONDS` | `60` | How often late-arriving events are materialized into the aggregates |

## Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | set by Compose | `postgresql://reqly:<password>@<host>:5432/reqly`. Add `?sslmode=require` for a remote database |
| `POSTGRES_PASSWORD` | `localdev` | Local Compose only |

The database must be TimescaleDB with the Toolkit: the `timescale/timescaledb-ha:pg16` image includes both. The collector applies its migrations on start-up.

## Dashboard

| Variable | Default | Notes |
|---|---|---|
| `VITE_COLLECTOR_URL` | `http://localhost:8000` | Collector URL **as seen from the browser**, at build time |
| `VITE_READ_KEY` | `demo-read-key` | At build time |
| `REQLY_COLLECTOR_URL` / `REQLY_READ_KEY` | — | The same two settings at run time, for the prebuilt image |

## Local demo traffic

| Variable | Default | Notes |
|---|---|---|
| `BACKFILL_WEEKS` | `8` | Weeks of synthetic history the load generator creates on first run |
| `BACKFILL_EVENTS_PER_HOUR` | `30` | Synthetic events per hour during the backfill |

SDK settings (`REQLY_SERVICE_NAME`, `REQLY_API_KEY`, …) are listed on the [Python](../instrument/python.md#configuration) and [Node.js](../instrument/node.md#configuration) pages.
