# Changelog

All notable changes to **Reqly** are documented here. Versions follow the collector
(`collector/pyproject.toml`); the SDK releases that shipped alongside are noted in each entry.
Per-package details: [Python SDK](sdk/CHANGELOG.md) · [Node SDK](sdk-node/CHANGELOG.md).

---

## [Unreleased]

### 🐛 Fixed
- LLM prices: the table is checked against the providers' pages (2026-10) and covers current
  OpenAI, Anthropic, Google and Groq models. A model only matches its own entry or a dated
  snapshot of it, so `gpt-4o-mini`-style variants and new models are no longer priced as a
  similar-looking model

### 🎊 Improved
- Helm: the collector waits for the bundled database instead of restarting while it starts

---

## [0.11.0] — 2026-10-10

Collector 0.11.0 · reqly-node 0.2.0 · Helm chart 0.11.0

### ✨ Added
- **LLM cost alerts:** each hour, a route's LLM spend is compared with the same weekday-hour
  over 8 weeks. A spike in cost per request or in LLM traffic opens an alert that says which
  factor rose (requests, tokens per call, price per token, or a different model). Floor:
  `LLM_COST_ALERT_MIN_USD`, default $1 extra per hour
- Node SDK: `reqlyKoa()` for Koa 2/3 and `reqlyNest(app)` for NestJS on the Express or
  Fastify adapter
- **Helm chart** (`oci://ghcr.io/tanisheesh/charts/reqly`): collector, dashboard and an optional
  TimescaleDB, with generated keys that survive upgrades and an optional ingress
- A docs site at [reqly.tanisheesh.in/docs](https://reqly.tanisheesh.in/docs/)

### 🎊 Improved
- SDK overhead is measured on every SDK change (`bench/`), and both SDKs got faster as a
  result: Python SDK 0.5.2 roughly halves the per-request cost (Flask +63 → +33 µs), and
  reqly-node 0.1.2 sends one collector request per batch instead of one per app request
  under steady traffic
- The landing page covers the whole product and works on every screen size

---

## [0.10.0] — 2026-10-10

### ✨ Added
- Projects and per-project API keys (`rqk_…`) with `ingest` / `read` / `admin` scopes; a new
  service joins the project of the key that first sends it
- Dashboard **Settings** for projects, services, API keys, members and your password, and a
  project switcher in the header

## [0.9.0] — 2026-10-10

### ✨ Added
- Dashboard sign-in with users and sessions (argon2id passwords, 7-day bearer sessions);
  `PUBLIC_DASHBOARD=false` makes the dashboard private

## [0.8.1] — 2026-10-10

Python SDK 0.5.1 · reqly-node 0.1.1

### 🐛 Fixed
- A single stray old event could replace days of aggregate history; ingest now refuses events
  older than 13 days unless the batch is an explicit backfill, or more than 15 minutes ahead
- Scheduled jobs no longer get skipped when the event loop is busy
- Regenerating the weekly report is cached for 10 minutes, so it can't run up LLM cost
- SDK recording can never raise into the app; Hono `app.all` routes, double registration and
  the flush on exit fixed in the Node SDK

### 🔒 Security
- Request bodies are capped at 16 MB, chunked bodies included

## [0.8.0] — 2026-10-10

Python SDK 0.5.0 · reqly-node 0.1.0

### ✨ Added
- **API consumers:** who is calling, from an API-key header or your own logic, hashed in the
  SDK; top consumers, one consumer's routes, and who an incident hit
- **LLM cost per route:** record token usage; see tokens and cost per route, model and day
- **reqly-node**, a Node.js SDK for Express, Fastify and Hono
- Python SDK: `instrument_wsgi` / `instrument_asgi` for any WSGI or ASGI app
- Container images on GitHub Packages (`ghcr.io/tanisheesh/reqly-collector`, `reqly-dashboard`)

## [0.7.0] — 2026-10-10

Python SDK 0.4.0

### ✨ Added
- **OpenAPI drift:** upload a spec and see undocumented, unused and deprecated-but-called
  endpoints; FastAPI and Litestar apps can push their own spec (`push_openapi=True`)

## [0.6.0] — 2026-10-10

### ✨ Added
- **Ask Reqly:** questions in plain English, answered through read-only query tools, with every
  query shown and every number checked against the results

## [0.5.0] — 2026-10-10

Python SDK 0.3.0

### ✨ Added
- **SLOs:** availability and latency objectives with error budgets and burn-rate alerts
- Python SDK: Starlette, Litestar and Django integrations

## [0.4.0] — 2026-10-09

### ✨ Added
- Hourly anomaly alerts to Slack, Discord or a webhook, with root-cause hints (host,
  environment, status code, error type); open alerts on the dashboard

## [0.3.0] — 2026-10-09

Python SDK 0.2.0

### ✨ Added
- Releases on every event (auto-detected from CI variables), deploy markers, and the running
  release attached to every anomaly
- OTLP/HTTP trace ingest, so any language can report through OpenTelemetry
- Python SDK sends release, environment and request/response sizes

### 🎊 Improved
- Latency percentiles from mergeable sketches (TimescaleDB Toolkit), so service-level and
  7-day p95/p99 are real percentiles

### 🔒 Security
- TLS on the production Postgres

## [0.2.0] — 2026-10-09

Python SDK 0.1.5

### ✨ Added
- Late-arriving events (backfills, SDK retries) are materialized into the aggregates

### 🐛 Fixed
- Top-routes error rate was weighted by active minutes
- Anomaly detection is robust to noise and to testing many routes at once
- Python SDK retries 5xx responses, survives pre-fork servers and ignores a second
  `instrument()` call
- Dashboard error-rate labels, error states and axis dates

### 🔒 Security
- The read key is separate from the ingest key and never falls back to it; ingest field
  lengths are capped

## [0.1.x] — 2026-04-01 to 2026-06-26

### ✨ Added
- First version: Python SDK for FastAPI and Flask, collector with batched ingest and rate
  limiting, TimescaleDB continuous aggregates for p50/p95/p99 latency, error rates and status
  codes, the React dashboard, and a weekly AI anomaly report via Groq

---

[Unreleased]: https://github.com/tanisheesh/reqly/compare/collector-v0.11.0...HEAD
[0.11.0]: https://github.com/tanisheesh/reqly/tree/collector-v0.11.0
[0.10.0]: https://github.com/tanisheesh/reqly/commit/0d3fc17
[0.9.0]: https://github.com/tanisheesh/reqly/commit/ff10848
[0.8.1]: https://github.com/tanisheesh/reqly/tree/collector-v0.8.1
[0.8.0]: https://github.com/tanisheesh/reqly/commit/ae5b303
[0.7.0]: https://github.com/tanisheesh/reqly/commit/373aad3
[0.6.0]: https://github.com/tanisheesh/reqly/commit/a322fef
[0.5.0]: https://github.com/tanisheesh/reqly/commit/aef3324
[0.4.0]: https://github.com/tanisheesh/reqly/commit/a59bd29
[0.3.0]: https://github.com/tanisheesh/reqly/commit/b18e5cc
[0.2.0]: https://github.com/tanisheesh/reqly/commit/fb988e1
[0.1.x]: https://github.com/tanisheesh/reqly/commit/051b38e

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
