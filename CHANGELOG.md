# Changelog

All notable changes to **Reqly** are documented here. Versions follow the collector
(`collector/pyproject.toml`); the SDK releases that shipped alongside are noted in each entry.
Per-package details: [Python SDK](sdk/CHANGELOG.md) · [Node SDK](sdk-node/CHANGELOG.md).

---

## [0.14.2] — 2026-10-19

Collector 0.14.2 · Helm chart 0.14.2 · Node SDK 0.5.2

- Ask Reqly's `get_alerts` reads client error alerts properly (the status code, its share of requests,
  the release, sample traces); it used to read them as error-rate anomalies and return empty numbers
- `docker compose` passes every setting in `.env` to the collector (and the all-in-one compose file
  too): the alert channels, `METRICS_TOKEN`, `LOG_FORMAT`, the trace settings, `REQLY_CONSUMER_SALT`,
  the rate limits and more were set in `.env` but never reached it. Needs Compose 2.24+
- Node: batches are gzipped on libuv's thread pool instead of the event loop, fixing a 3-4 µs per-request
  regression since 0.4.0 (found by re-running the benchmark, whose CI job had stopped failing on a crash)
- Docs brought up to date with 0.13-0.14: README, landing page, PRD, architecture, decisions 13-17, setup,
  deploy, HTTP API reference (alert actions, `/metrics`), SDK guides and benchmarks

---

## [0.14.1] — 2026-10-19

Collector 0.14.1 · Helm chart 0.14.1 · Python SDK 0.7.1 · Node SDK 0.5.1

- Two collectors on one database (a rolling deploy, a second replica) no longer both run the
  scheduled jobs: each run claims its (job, slot) in `scheduled_runs` first (migration 014), so
  hourly alerts, SLO checks and the weekly AI report (and its LLM call) happen once
- LLM client instrumentation also counts the `stream()` helpers in Python
  (`client.messages.stream(...)`, `client.chat.completions.stream(...)`, `client.responses.stream(...)`)
- `trace_slow_ms` / `traceSlowMs` (`REQLY_TRACE_SLOW_MS`) in both SDKs, to match a lower `TRACE_ID_SLOW_MS`

---

## [0.14.0] — 2026-10-19

Collector 0.14.0 · Helm chart 0.14.0 · Python SDK 0.7.0 · Node SDK 0.5.0

### ⚠️ Breaking: safe defaults
Outside demo mode the collector is now locked down by default:
- it **refuses to start** without `REQLY_INGEST_KEY` and `REQLY_READ_KEY` (no more `demo-key` fallback)
- `PUBLIC_DASHBOARD` defaults to **`false`**: the dashboard needs a sign-in
- `CORS_ORIGINS` defaults to **empty**

`REQLY_DEMO_MODE=true` brings back the old defaults. `docker compose up` sets it, so the local
stack works as before; the Helm chart sets everything explicitly and is unaffected.
**Upgrading a collector you run yourself?** Make sure both keys are set, and set
`PUBLIC_DASHBOARD=true` and `CORS_ORIGINS` explicitly if you relied on the old defaults.

### ✨ Added
- **All-in-one image** `ghcr.io/tanisheesh/reqly`: the collector serves the dashboard on the same
  port (`DASHBOARD_DIR`), so a deployment is that image plus TimescaleDB, with no CORS and no
  collector URL baked into the dashboard. `deploy/all-in-one/docker-compose.yml` runs both
- **On-call channels:** Microsoft Teams (Workflows webhook, Adaptive Card), PagerDuty (Events API
  v2, one incident per alert, resolved with it), Opsgenie (closed on resolve) and email (SMTP or
  Resend). Helm secrets for each
- **Acknowledge, silence, mute:** acknowledge stops reminders; silence stops an alert's
  notifications for up to 7 days; mute stops a service's, a route's or a kind's for up to 30 days.
  Buttons on the dashboard for signed-in users, and `POST /v1/alerts/{id}/acknowledge`,
  `/silence`, `/v1/alert-mutes`. The public read key can't use them
- **Client error alerts:** a sudden spike of 401 / 403 / 422 / 429 on a route (same weekday-hour
  baseline and Poisson test as the error alerts) opens a `client_errors` alert, with the release
  that was running. `CLIENT_ERROR_ALERT_STATUSES` picks the codes
- **Trace correlation:** failed (4xx/5xx) and slow requests keep their trace id (SDKs read
  `traceparent`, `sentry-trace` or `x-b3-traceid`; OTLP spans have one), and error and client
  error alerts list up to 5 of that hour's traces. `TRACE_URL_TEMPLATE` makes them links in the
  dashboard; `TRACE_ID_SLOW_MS` sets "slow" (default 1000)
- **LLM client auto-instrumentation:** `reqly.instrument_openai(client)` /
  `instrument_anthropic(client)` and `instrumentOpenAI` / `instrumentAnthropic` in Node count every
  call's tokens (chat, Responses, completions, embeddings, Anthropic messages, streams included)
  for the request that made it, without `record_llm_usage()`

### ⬆️ Upgrading
- Migrations 012 (alert actions) and 013 (`trace_id` column) run on start-up
- Ingest spec v4: the event's optional `trace_id`

---

## [0.13.0] — 2026-10-19

Collector 0.13.0 · Helm chart 0.13.0 · Python SDK 0.6.0 · Node SDK 0.4.0

### ✨ Added
- **Any OpenAI-compatible LLM** for the weekly report and Ask Reqly: `LLM_BASE_URL`, `LLM_API_KEY`
  and `LLM_MODEL` point the collector at OpenAI, OpenRouter, a local Ollama or vLLM, or anything
  else that speaks the chat completions API. `GROQ_API_KEY` alone still means Groq, as before, and
  is only ever sent to Groq. Helm: `secrets.llmApiKey`
- **Multi-model LLM cost:** a request that calls several models now prices each model's tokens at
  that model's rate, instead of all of them at the rate of the model with the most tokens. The
  SDKs send each model's tokens (`llm_usage`, ingest spec v3); OTLP GenAI spans get the same. Past
  data and older SDKs keep the old attribution
- **The collector's own metrics** at `GET /metrics` (Prometheus): requests and latency per
  collector route, events ingested and rejected per source, database pool connections and the
  late-data backlog, with no service, project or consumer names in them. `METRICS_TOKEN` puts
  them behind a bearer token, `METRICS_ENABLED=false` turns them off
- **`LOG_FORMAT=json`:** one JSON object per log line, uvicorn's access lines included
- **Gzip ingest:** `/v1/ingest` accepts `Content-Encoding: gzip` (capped at 32 MB decompressed),
  and both SDKs compress batches above 1 KB. Against an older collector they resend the batch as
  plain JSON and stop compressing, so mixed versions keep working

### ⚡ Performance
- A batch is inserted with one `unnest()` statement instead of one `INSERT` per event
- A clean batch is validated in one call; events are only validated one by one to find the bad
  ones when that fails
- OTLP protobuf exports are mapped straight from the parsed message, without converting it to a
  dict first (about 3× faster on a real Express export)
- With the TimescaleDB Toolkit, `route_latency_1min` is no longer refreshed every minute: nothing
  reads it once the sketch aggregate exists (migration 010)

### 🔧 Changed
- The collector image installs its dependencies from a hash-pinned lock
  (`collector/requirements.txt`), so every build gets the same packages; CI checks it matches
  `pyproject.toml`
- The `groq` package is replaced by `openai` (the client for every provider)
- CI: ruff and mypy for the collector and Python SDK, dashboard unit tests (vitest), and an end to
  end test that sends from both SDKs through a real collector and TimescaleDB to the read API

### ⬆️ Upgrading
- Migrations 010 and 011 run on start-up. Nothing to change in your settings
- `/metrics` is on and unauthenticated by default. It holds no tenant data, but set
  `METRICS_TOKEN` (or `METRICS_ENABLED=false`) if the collector is reachable from the internet

---

## [0.12.3] — 2026-10-19

Collector 0.12.3 · Helm chart 0.12.3

### ✨ Added
- **Environment filter:** `?environment=` on `/v1/metrics/summary`, a new
  `GET /v1/services/{service}/environments`, and an Environment dropdown in the dashboard, so
  production and staging traffic aren't mixed in the charts (needs the TimescaleDB Toolkit)
- OpenTelemetry apps get API consumers: `enduser.id` (or `reqly.consumer_id`) on the server span,
  HMAC-hashed by the collector with `REQLY_CONSUMER_SALT` (the SDKs' formula) and capped per day
  like ingest. Without the salt the attribute is ignored, so a raw user id is never stored

### 🐛 Fixed
- Ingest was rate-limited per client IP, so services behind one NAT or in one cluster shared a
  single 600/minute budget. It's now per key (`RATE_LIMIT_PER_MINUTE` per project key,
  `RATE_LIMIT_ENV_KEY_PER_MINUTE` for the shared env key) under a per-IP cap
  (`RATE_LIMIT_INGEST_PER_IP_PER_MINUTE`). Ask Reqly, insights and sign-in stay per IP
- Top routes for the 1h window scanned raw events even with the sketch aggregate available; the
  service and route lists read the older view. Both use `api_latency_1min` now

---

## [0.12.2] — 2026-10-19

Collector 0.12.2 · Helm chart 0.12.2 · Python SDK 0.5.6

### 🐛 Fixed
- OpenAPI spec upload (`push_openapi`) failed with project keys: it needed the `admin` scope, and
  even an admin project key got a 403 on a brand-new service, because the SDK uploads on the first
  request, before its first batch has claimed the service. Upload now takes the `ingest` (or
  `admin`) scope and claims a new service for the uploader's project, like ingest does
- Ingest with the env key no longer queries the database for the default project on every batch
- Two collectors starting at once (a rolling deploy) can't race on migrations: an advisory lock
  serializes them

### ✨ Added
- `GET /v1/ready`: readiness that checks the database (503 when it doesn't answer); `/v1/health`
  stays a liveness check. The Helm chart's readiness probe uses it
- The collector image runs as a non-root user (uid 1000, matching the Helm chart)

### ✨ Added
- Python SDK: `push_openapi` takes the spec (a dict or a function returning it), so Flask, Django
  and generic WSGI/ASGI apps can upload it for API drift, as the Node SDK already could

### 💀 Removed
- The optional AWS Lambda for the weekly report (`infra/sam`) and the collector's unused Lambda
  adapter (`mangum`). The Lambda had fallen behind the collector's report (no affected consumers,
  no API drift); the collector runs the weekly report itself. The EC2 database setup stays

### ♻️ Changed
- Python SDK: `capture_request_body`, which never did anything, logs that it is ignored and will be removed

---

## [0.12.1] — 2026-10-11

Collector 0.12.1 · Python SDK 0.5.5 · reqly-node 0.3.2 · Helm chart 0.12.1

### 🔒 Security
- A `backfill` batch rebuilt every aggregate hour from its oldest event up to now, for every
  service. Hours older than the 14-day raw retention were rebuilt from nothing, so one old
  event could erase weeks of history, and any project key could do it to other projects. A
  backfill now rebuilds only the hours it contains and needs the collector's `REQLY_INGEST_KEY`
- Alert messages neutralize `<`, `>` and backticks in service names, routes, hosts and other
  ingested values, so they can't carry Slack mentions or links; Discord alerts can't ping
  `@everyone`
- `POST /v1/insights/generate` returns 404 for a service with no data instead of storing a report

### 🐛 Fixed
- SDKs: with the collector down or not answering, app shutdown could wait 20-90+ s; the flush at
  exit now takes at most 5 s
- SDKs: a request the client abandoned is recorded as 499 instead of a 500 error (Python) or the
  handler's 200 (Node)
- SDKs: out-of-range batch size, queue size and flush interval are clamped (a Node batch size
  of 0 looped forever; a Python interval of 0 spun a CPU core)
- Helm: installing the chart from a clone of the repository deployed 0.11.0 images (a stale
  `appVersion`); it now matches the collector, and CI fails if it falls behind

### ♻️ Changed
- **License split:** the SDKs (`reqly`, `reqly-node`) are now **MIT**, so they can go into any
  app; the collector, dashboard and Helm chart are **AGPL-3.0** (were GPL-3.0). Earlier releases
  keep the license they were published under

---

## [0.12.0] — 2026-10-11

Collector 0.12.0 · Python SDK 0.5.3 · reqly-node 0.3.0 · Helm chart 0.12.0

### ✨ Added
- Node SDK: `reqlyHttp()` for plain node:http or any framework without an integration (with a
  `routeResolver`), and Bun support (Hono on `Bun.serve`, node:http), tested in CI
- Node SDK: `pushOpenapi` uploads the app's spec (Fastify via @fastify/swagger, NestJS document,
  or any spec), and streamed response bodies are measured
- OpenTelemetry apps get LLM cost: GenAI spans (`gen_ai.usage.*`) are added to the HTTP request
  they ran under, so cost per route and LLM cost alerts work without a Reqly SDK
- A cap on distinct API consumers: the first `CONSUMER_LIMIT_PER_DAY` (1,000) per service per day
  keep their id, later new ones are stored as `__other__`, so a misconfigured consumer id
  can't flood the data
- The weekly report has an API surface line when the service has an OpenAPI spec: undocumented
  endpoints with traffic, unused ones, and deprecated ones still called (and by whom)

### 🐛 Fixed
- Python SDK: heavy traffic (~400+ requests/s per process) no longer drops events between
  flushes; a full batch ships right away
- Node SDK: in Express and Fastify a 4xx error object no longer counts as an error; Koa includes a
  stream body's time and size
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

[Unreleased]: https://github.com/tanisheesh/reqly/compare/collector-v0.14.2...HEAD
[0.14.2]: https://github.com/tanisheesh/reqly/tree/collector-v0.14.2
[0.14.1]: https://github.com/tanisheesh/reqly/tree/collector-v0.14.1
[0.14.0]: https://github.com/tanisheesh/reqly/tree/collector-v0.14.0
[0.13.0]: https://github.com/tanisheesh/reqly/tree/collector-v0.13.0
[0.12.3]: https://github.com/tanisheesh/reqly/tree/collector-v0.12.3
[0.12.2]: https://github.com/tanisheesh/reqly/tree/collector-v0.12.2
[0.12.1]: https://github.com/tanisheesh/reqly/tree/collector-v0.12.1
[0.12.0]: https://github.com/tanisheesh/reqly/tree/collector-v0.12.0
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
<a href="mailto:hi@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hi%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
