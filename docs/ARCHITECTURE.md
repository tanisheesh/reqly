# Reqly — Architecture

Companion to [PRD.md](PRD.md): the PRD says what Reqly does, this says how.

---

## 1. Stack

| Layer | Tech |
|---|---|
| Python SDK | Python 3.9+ · pure ASGI middleware (FastAPI, Starlette, Litestar) · Flask hooks · Django middleware · generic WSGI/ASGI wrappers · httpx · one background flush thread |
| Node.js SDK | TypeScript, Node 20+, no runtime dependencies · Express / Fastify / Hono / Koa middleware, NestJS helper · `AsyncLocalStorage` · `fetch` |
| Collector | FastAPI · Uvicorn · asyncpg · APScheduler 3 · slowapi · Pydantic v2 · argon2-cffi · opentelemetry-proto · httpx |
| Database | TimescaleDB on PostgreSQL 16 (`timescale/timescaledb-ha`, with the Toolkit) · hypertables · continuous aggregates · UddSketch · retention policies |
| Dashboard | React 19 · Vite · TypeScript · Tailwind CSS 4 · Recharts · TanStack Query |
| AI | Groq API · `openai/gpt-oss-120b` (reports and tool calling) · statistics-first detection (Poisson tail / z-score) |
| Infra (local) | Docker Compose: timescaledb, collector, dashboard, load-generator |
| Infra (Kubernetes) | Helm chart `deploy/helm/reqly`: collector, dashboard, optional TimescaleDB StatefulSet; published to `oci://ghcr.io/tanisheesh/charts` |
| Infra (live demo) | TimescaleDB on an EC2 t3.micro (TLS) · collector and dashboard on Render · GHCR images · PyPI and npm trusted publishing |

---

## 2. Components

```
reqly/
  sdk/                   Python SDK (PyPI: reqly)
  sdk-node/              Node.js SDK (npm: reqly-node)
  collector/app/         FastAPI collector
    routers/             ingest, otlp, metrics, alerts, slos, insights, ask, openapi, usage, projects, auth
    db/                  pool + migrations runner, queries, late-data refresher
    insights/ alerts/    detection, release context, hints, weekly report, hourly + SLO alerts, notifier
    ask/                 Ask Reqly agent, tools, number verification
    slo/ openapi/ consumers/ llm/ projects/ users/   feature modules
  collector/migrations/  001-009 SQL, applied on start-up (tracked in schema_migrations)
  dashboard/             React SPA
  load_generator/        demo traffic with deliberate incidents (bad deploy, bad pod, Monday spike)
  bench/                 SDK overhead benchmarks
  demo/                  EventFlow, the instrumented demo app
  infra/                 EC2 user-data, optional AWS SAM stack for the weekly report, deploy guide
```

### SDKs (`sdk/`, `sdk-node/`)

Middleware records each request when the response finishes: method, the framework's matched route template (never the raw path; no match → `__unmatched__`), status, duration, error type, sizes, an optional consumer id (from a header or a function, HMAC-hashed with the app's salt) and LLM tokens recorded during the request (`record_llm_usage`). The event goes into a bounded in-memory queue (oldest dropped when full) and the request returns; a background flusher sends batches to `/v1/ingest` with short timeouts and retries on 408/429/5xx (the collector dedups on `event_id`). Anything that fails inside the SDK is logged and never raised into the app. Release and environment are sent once per batch, the release auto-detected from CI variables (`GITHUB_SHA`, `RENDER_GIT_COMMIT`, ...). Per-request overhead is measured in [bench/](../bench/README.md).

### Collector (`collector/app/`)

One FastAPI service:

- **Ingest:** `POST /v1/ingest` (SDK batches, per-event validation) and `POST /otlp/v1/traces` (OTLP/HTTP; HTTP server spans become events, and GenAI spans under them add the request's LLM model and tokens). Both refuse events older than 13 days (unless the batch is a backfill) or in the future, and enforce which project a service belongs to.
- **Reads:** metrics summary, services, routes, releases, alerts, SLOs, consumers, LLM usage, OpenAPI drift, latest report — reading from the continuous aggregates, raw events only where freshness or detail requires it.
- **Configuration:** SLOs, OpenAPI specs, projects, API keys, members (admin).
- **AI:** Ask Reqly and on-demand weekly reports.
- **Auth:** sign-in, sessions, password changes.

On start it creates the asyncpg pool, applies pending migrations, creates the first admin if configured, and starts two background loops: the late-data refresher (materializes events older than the aggregates' look-back, e.g. backfills, in 7-day slices) and APScheduler (weekly report, hourly anomaly check at :15, SLO check every 5 minutes).

### TimescaleDB

`request_events` is a hypertable (1-day chunks, 14-day retention). Continuous aggregates roll it up: `api_latency_1min` (UddSketch per minute, service, environment, route and method — the main source of latency and error charts; 90 days), the older `route_latency_1min`, `route_errors_1hour` and `route_status_distribution_1hour` (also the fallback when the Toolkit is absent), and `consumer_usage_1hour` / `llm_usage_1hour` (90 days). Configuration and state live in plain tables (see §4).

### Dashboard (`dashboard/src/`)

A static React SPA that calls the collector from the browser with a session token (signed-in users) or the read key (public dashboard). TanStack Query polls every 30–60 s. Panels for SLOs, consumers, LLM cost, API surface and alerts only appear when their data exists. The prebuilt image reads `REQLY_COLLECTOR_URL` / `REQLY_READ_KEY` at start-up, so one image works for any collector.

### Hourly alerts

At :15 past every hour the collector refreshes the last completed hour in `route_errors_1hour` and compares it with the same weekday-hour in the previous 8 weeks using the same detector as the weekly report (`recent_window=1h`). Anomalies get the same release context and hints, then `app/alerts/hourly.py` keeps at most one open alert per (service, route) in the `alerts` table: a new detection opens one and notifies, repeats update it (reminder every `ALERT_RENOTIFY_HOURS`), and two clean hours resolve it with a final notification. Messages go to Slack, Discord and/or a generic JSON webhook, built from the statistics only (no LLM in the alert path). Run the scheduler on a single collector instance.

### SLOs and error budgets

An SLO is one objective for a service or route over a window (default 28 days): `availability` (share of requests without an error) or `latency` (share at or under a threshold). The window's SLI and error budget come from `api_latency_1min` (latency via `approx_percentile_rank` on the merged sketch; without the Toolkit, from raw events covering at most 14 days). Burn rates for 5m / 30m / 1h / 6h come from raw events, which are current to the second. Every 5 minutes `app/alerts/slo_alerts.py` applies the multi-window burn-rate rules from the Google SRE workbook — fast burn when the 1h **and** 5m burn rates are ≥ 14.4, slow burn when the 6h **and** 30m rates are ≥ 6, with at least 20 requests in the longer window — and keeps one open alert per SLO (`kind = 'slo'` in `alerts`), notified through the same channels as anomaly alerts and resolved after 30 minutes out of burn.

### Consumers

The SDK tags each request with a consumer id (from a header or a callable), HMAC-SHA256-hashed with the app's salt before it leaves the process, so the collector never holds API keys. Consumer ids stay out of the minute-level aggregates (their cardinality is unbounded): windows up to 7 days read the raw events, 30 days the hourly rollup `consumer_usage_1hour` (service, consumer, route, method). They answer three questions: who uses the API (top consumers), who an incident hit (hourly alerts and weekly anomalies carry `affected_consumers`: how many of the consumers active on the route that hour got errors, and the top ones — also in the Slack/Discord message), and who still calls a deprecated operation (drift report).

### LLM cost per route

Requests carry the model and token counts recorded with `reqly.record_llm_usage()` (a request that called several models is attributed to the one with the most tokens, with all tokens summed). `llm_usage_1hour` rolls them up per route and model for 90 days. Cost is computed at query time from a price table (`app/llm/llm_prices.yaml`, USD per 1M tokens, longest-prefix model match; `LLM_PRICES_FILE` overrides and extends it), so a price edit applies to past usage, and models without a price are listed as unpriced rather than guessed.

Right after the hourly anomaly check, `app/alerts/llm_cost.py` refreshes the last hour of `llm_usage_1hour` and `app/llm/anomalies.py` compares each route's cost with the same weekday-hour over 8 weeks: median and MAD of cost per request, and of requests. It flags a **unit-cost** spike (≥ 2× the usual cost per request and ≥ 4 robust deviations) or a **volume** spike (≥ 3× the usual requests at a normal cost per request) when the extra spend clears `LLM_COST_ALERT_MIN_USD` (default $1). Each finding lists the factors that rose (requests, share of requests calling a model, tokens per call, blended price per 1M tokens) and the model that now carries the cost if it changed. Alerts use the shared alerts table (`kind = 'llm_cost'`) and lifecycle.

### OpenAPI drift

A service's spec (`api_specs`, one per service, uploaded with the ingest key or by the SDK's `push_openapi`) is compared with the (method, route) pairs seen in the last 30 days of `api_latency_1min` (14 days of raw events without the Toolkit) by `app/openapi/drift.py`. Paths are compared by shape, so every framework's parameter syntax lines up — `{user_id}`, `{id}`, `:id` and `<int:id>` are all `{}` — and an un-templated route such as `/users/42` from an OTLP exporter still matches `/users/{}`; a literal spec segment beats a parameter (`/users/me` over `/users/{id}`). The report lists **undocumented** operations (traffic, not in the spec; HEAD/OPTIONS and `__unmatched__` 404s are left out), **unused** ones (in the spec, no traffic) and **deprecated** ones still being called, plus coverage and the share of traffic that is undocumented. The dashboard shows it when a spec exists, and Ask Reqly can query it (`get_api_drift`).

### Ask Reqly

`POST /v1/ask {service_name, question}` answers questions like "why was /orders slow yesterday afternoon?" with Groq tool calling (`app/ask/`). The model never writes SQL: it chooses among nine read-only tools — `get_stats` (totals or per route/hour/day, from the sketch aggregate), `compare_periods`, `get_breakdown` (by host, environment, release, status code, error type, method or consumer, from raw events), `list_releases`, `get_alerts`, `get_slos`, `get_api_drift`, `get_consumers` and `get_llm_costs` — whose arguments are validated and whose time ranges are clamped to retention. Results are rounded aggregates, never raw events. The system prompt carries the current UTC time, the dates of the last 8 days, the service's routes and its recent releases, so relative times ("last Monday") and deploys resolve without a lookup. At most 6 tool calls per question; after that the model must answer from what it has. The response includes every tool call and its result, and the dashboard shows them under the answer. Every number in the answer is then looked up in those results (as written, as a percentage, ms as seconds, a ratio as a percent change, at the written precision); the ones not found come back as `unverified_numbers` and the dashboard flags them, since models occasionally mis-copy digits. The read key is public to dashboard viewers, so the endpoint is limited to 5 questions a minute per IP and `ASK_DAILY_LIMIT` (default 200) per collector per day. An eval set over the load generator's scenarios lives in `collector/tests/ask_eval/` (run by hand; it needs a Groq key).

### AI Insights Pipeline

1. APScheduler triggers weekly, Sunday 23:00 UTC (or on demand: `POST /v1/insights/generate`, cached for 10 minutes).
2. Pulls 8 weeks of hourly aggregates from `route_errors_1hour`.
3. `anomaly_detection.py` builds a day-of-week × hour-of-day baseline from the older 7 weeks and compares the most recent 7 days. Error rates use an exact Poisson tail test on the request/error counts; p95 is only tested on hours with ≥ 100 requests. A cell is flagged at z > 4.0 (≈ Bonferroni for ~1,700 cells per run) and only if the shift is material (≥ 2pp of errors or ≥ 50% p95), with ≥ 3 baseline samples.
4. The top 5 anomalies by z-score are kept, each with the hour it happened (`window_start`).
5. `deploys.py` adds `release_context`: which release served that route in that hour, whether it was first seen within the week before, and for a new release the previous one plus before/after error rate and p95.
6. `hints.py` adds deterministic root-cause `hints`: errors or slow requests concentrated on one host/environment relative to its traffic share, or an error type / status code that dominates and was rare the week before.
7. `consumers/queries.py` adds `affected_consumers`: how many of the consumers active on that route in that hour got errors, and the top ones.
8. With `GROQ_API_KEY` set, the anomaly JSON goes to `openai/gpt-oss-120b` (temperature 0.3, at most 2 000 tokens including reasoning) with a system prompt that forbids inventing root causes; otherwise the findings are formatted as plain text.
9. The result is upserted into `insight_reports`.

---

## 3. Data Flow

```
[App + Reqly SDK] --batch every 5 s--> POST /v1/ingest ---+
[App + OTel SDK]  --OTLP/HTTP-------> POST /otlp/v1/traces +--> validate, age check, project check
                                                                --> INSERT request_events (+ deployments)
                                                                        |
                         continuous aggregates (policies + late-data refresher)
                         api_latency_1min, route_*_1hour, consumer/llm_usage_1hour
                                                                        |
[Dashboard] --GET /v1/metrics/summary, /consumers, /llm-usage, ...------+--> charts, tables, panels
[Dashboard] --POST /v1/ask--> Groq tool calls --> read-only queries --> answer + verified numbers

[Scheduler] :15 hourly   --> last hour vs 8 weekday-hours (errors, p95, LLM cost) --> alerts table --> Slack / Discord / webhook
            every 5 min  --> SLO burn rates               --> alerts table --> same channels
            Sunday 23:00 --> week vs baseline --> Groq narrative --> insight_reports
```

1. The SDK (or an OpenTelemetry exporter) sends request events; the collector validates each, refuses ones too old or too new, and checks that the key may write that service.
2. Accepted events go into `request_events`; release/environment pairs update `deployments`.
3. Aggregate policies roll the events up within a minute; older stragglers are refreshed by the late-data loop.
4. The dashboard reads the aggregates for charts and panels; Ask Reqly reads them through its tools.
5. The scheduler turns anomalies and SLO burn into alerts and notifications, and writes the weekly report.

---

## 4. Database Schema

- `request_events` — hypertable: `event_id`, `time`, `service_name`, `method`, `route`, `status_code`, `duration_ms`, `is_error`, `error_type`, `host`, and (schema v2) `release`, `environment`, `consumer_id`, `request_bytes`, `response_bytes`, `llm_model`, `llm_input_tokens`, `llm_output_tokens`. PK `(time, event_id)`, 14-day retention.
- `api_latency_1min` — continuous aggregate (with the Toolkit): `uddsketch(1000, 0.005)` of duration plus request and error counts per minute, service, environment, route, method. 90 days.
- `route_latency_1min`, `route_errors_1hour`, `route_status_distribution_1hour` — the original aggregates (90 / 180 / 180 days); `route_errors_1hour` feeds the anomaly detector.
- `consumer_usage_1hour`, `llm_usage_1hour` — hourly rollups by consumer / by LLM model (90 days).
- `deployments` — first and last seen per service, environment and release.
- `insight_reports` — one weekly report per service (`anomalies_json`, `report_text`).
- `alerts` — `kind` (`anomaly`, `slo` or `llm_cost`), service, route, first/last hour, resolved time, details JSON; at most one open alert per service, route and kind.
- `slos` — objective (`availability` / `latency`), target, threshold, window per service or route.
- `api_specs` — one OpenAPI spec (JSONB) and base path per service.
- `users`, `sessions` — argon2id password hashes; SHA-256 of session tokens with expiry.
- `projects`, `project_services`, `api_keys`, `project_members` — service ownership, hashed keys with scopes, membership.

The ingest contract is specified in the [ingest spec](https://reqly.tanisheesh.in/docs/reference/ingest-spec/).

**Indexes:** `request_events(service_name, route, time DESC)` for per-route queries; a partial index on `request_events(service_name, time DESC) WHERE is_error` for error scans; unique `alerts(service_name, route, kind) WHERE resolved_at IS NULL` for alert dedup; unique `api_keys(key_hash)` for key lookups.

---

## 5. AI / LLM Design

### Input

**Weekly report:** structured JSON of pre-computed anomalies — never raw events. Each anomaly has `route`, `day_of_week`, `hour_range`, `observed_error_rate`, `baseline_error_rate`, `observed_p95_ms`, `baseline_p95_ms`, `z_score`, plus `release_context`, `hints` and `affected_consumers` when available.

**Ask Reqly:** the user's question, a system prompt with the current UTC time, the dates of the last 8 days, the service's routes and recent releases, and the results of the tool calls the model makes (rounded aggregates only). See [Ask Reqly](#ask-reqly) for the tools, limits and number verification.

### System prompt strategy

The system prompt instructs the model to write a concise 3–6 bullet report from the pre-computed anomalies only. It explicitly forbids inventing root causes not supported by the data, requires causal explanations to be phrased as hypotheses ("likely due to", "consistent with"), and instructs the model to state plainly that no anomalies were found if the list is empty — not to invent a problem to seem useful.

### Response schema

```jsonc
// Free-form markdown text (3-6 bullet points)
// No JSON schema enforced on the LLM output — it's display-only narrative.
// The upstream anomaly detection result (structured JSON) is the auditable artifact.
```

### Validation

The Groq response is used as-is for display. The anomaly JSON that feeds it is the authoritative record — validated and stored in `insight_reports.anomalies_json`.

### Failure handling

Groq call has a 30 s timeout. On any exception (timeout, rate limit, provider outage), the pipeline falls back to `_fallback_report()` which formats the raw statistical findings as plain text. The insights panel is "unformatted", not "broken". If `GROQ_API_KEY` is absent entirely, the fallback runs immediately without attempting any API call.

---

## 6. API Routes

| Method | Route | Auth | Description |
|---|---|---|---|
| `GET` | `/v1/health` | None | Liveness probe — returns `{"status": "ok"}` |
| `GET` | `/v1/auth/config` | None | Whether the dashboard is public (`PUBLIC_DASHBOARD`) |
| `POST` | `/v1/auth/login` | None (10/min per IP) | Username + password → session token (bearer) |
| `POST` | `/v1/auth/logout` | Session | Ends the session |
| `GET` | `/v1/auth/me` | Session | The signed-in user |
| `POST` | `/v1/auth/password` | Session | Changes the password and signs out every session of the user |
| `GET` | `/v1/projects` | Read | Projects the caller can see, with their services |
| `POST` | `/v1/projects` | Admin of all projects | Create a project (`slug`, `name`) |
| `PUT` | `/v1/projects/{id}/services` | Admin of all projects | Move a service (and its data) to a project |
| `GET` / `POST` | `/v1/projects/{id}/keys` | Project admin | List keys / create one (`name`, `scopes`); the key is returned once |
| `POST` | `/v1/keys/{id}/revoke` | Project admin | Revoke a key |
| `GET` / `POST` / `DELETE` | `/v1/projects/{id}/members[/{user_id}]` | Admin of all projects | Who can read the project |
| `POST` | `/v1/ingest` | Ingest key | Batch ingest of request events (≤ 1 000 per call); partial-batch acceptance |
| `POST` | `/otlp/v1/traces` | Ingest key | OTLP/HTTP trace receiver (protobuf or JSON, gzip) — HTTP server spans become request events; see [OpenTelemetry](https://reqly.tanisheesh.in/docs/instrument/opentelemetry/) |
| `GET` | `/v1/services` | Read key or session | List all service names with recorded traffic |
| `GET` | `/v1/services/{service_name}/routes` | Read key or session | List all route templates for a service |
| `GET` | `/v1/metrics/summary` | Read key or session | Latency series, error rate series, status distribution, top routes, requests/min for a service+window |
| `GET` | `/v1/services/{service_name}/releases` | Read key or session | Recent releases with first/last seen, request volume, error rate and p95 (14-day raw window) |
| `GET` | `/v1/insights/latest` | Read key or session | Latest weekly AI report for a service |
| `GET` | `/v1/alerts` | Read key or session | Open (or recent, `status=all`) alerts — hourly anomalies and SLO burn — optionally per service |
| `GET` | `/v1/slos` | Read key or session | SLOs with live status: SLI, error budget left, burn rates (5m / 30m / 1h / 6h), state |
| `PUT` | `/v1/slos` | Ingest key or admin session | Create or update an SLO (by service + name) |
| `DELETE` | `/v1/slos/{id}` | Ingest key or admin session | Delete an SLO |
| `PUT` | `/v1/services/{svc}/openapi` | Ingest key or admin session | Upload the service's OpenAPI 3 / Swagger 2 spec (JSON or YAML); `?base_path=` prefixes its paths |
| `GET` | `/v1/services/{svc}/openapi/drift` | Read key or session | Spec vs the last 30 days of traffic: undocumented, unused and deprecated-but-used operations |
| `DELETE` | `/v1/services/{svc}/openapi` | Ingest key or admin session | Remove the spec |
| `GET` | `/v1/services/{svc}/consumers` | Read key or session | Top consumers (`window=24h\|7d\|30d`): requests, share, error rate, p95, routes |
| `GET` | `/v1/services/{svc}/consumers/{id}` | Read key or session | One consumer's routes and daily requests |
| `GET` | `/v1/services/{svc}/llm-usage` | Read key or session | LLM tokens and estimated cost per route, model and day |
| `POST` | `/v1/ask` | Read key or session | Ask Reqly: answers a question about one service from its data (5/min per IP, `ASK_DAILY_LIMIT` per day) |
| `POST` | `/v1/insights/generate` | Read key or session | Trigger insights generation on demand (rate-limited 5/min) |

---

## 7. Security

- **Users and sessions:** dashboard users sign in with a username and password (argon2id, hashed off the event loop; a login takes the same time whether or not the user exists). A session is a random 256-bit bearer token, stored only as its SHA-256, valid `SESSION_TTL_HOURS` (default 7 days); changing a password ends all of the user's sessions. Bearer tokens rather than cookies because the dashboard and collector are usually different sites, where browsers block third-party cookies. "Read" endpoints accept a session, or the read key while `PUBLIC_DASHBOARD` is on (a public demo); with it off the read key is refused. Changing SLOs and OpenAPI specs takes the ingest key or an admin's session. The first admin comes from `REQLY_ADMIN_PASSWORD` when there are no users; more users and password resets: `python -m app.users create-user NAME [--admin]` / `set-password NAME`.
- **Projects and API keys:** every service belongs to one project (`project_services`); services that had data before projects existed are in `default`, and a new service joins the project of the key that sends its first events — after that, another project's keys get 403 for it (OTLP: those spans are rejected as a partial success). Project keys (`rqk_` + 256 random bits, stored as SHA-256, prefix shown) carry scopes `ingest` / `read` / `admin` and only reach their project's services; revoking takes effect at once on the instance that did it and within 60 s elsewhere (lookup cache). Every request resolves to a principal — scopes plus the projects it may touch: the env ingest key (ingest + admin) and admin users (read + admin) reach every project, the public read key reads every project, other users read the projects they are members of. Endpoints about one service check it through the `service_name` in their path or query; list endpoints filter. Projects, service moves and memberships need an admin of every project; a project's keys can be managed by that project's admin keys. A valid key used for something its scopes don't allow gets 403 (an unknown key 401).
- **API keys:** `REQLY_INGEST_KEY` (write) and `REQLY_READ_KEY` (read) are separate keys, passed via the `X-Reqly-Key` header and compared in constant time. The read key is compiled into the dashboard bundle, so it is effectively public to dashboard viewers; it never falls back to the ingest key, and the collector warns at startup if the two are equal. Defaults: `demo-key` / `demo-read-key`, with a startup warning.
- **Ingest validation:** Every event in a batch is validated with Pydantic before writing. `duration_ms` is clamped (0–300 000 ms). Batch size is hard-capped at 1 000 events per call, and string fields are length-capped (`service_name` 128, `route` 512, `method` 16).
- **Rate limiting:** slowapi enforces 600 req/min per client IP on ingest; insights generation is separately limited at 5/min and returns this week's report as is if it was generated in the last 10 minutes (each generation is an LLM call on a public key). Behind a proxy, set `FORWARDED_ALLOW_IPS` so the real client IP is used.
- **Request size:** every request body is counted as it arrives and refused with 413 above 16 MB (`app/body_limit.py`), chunked bodies included — Starlette would otherwise buffer a body whole before any handler sees it.
- **Event age:** ingest and OTLP refuse events older than 13 days (unless the batch is an explicit `backfill`) or more than 15 minutes in the future. Raw events are kept 14 days and aggregates 90–180, so materializing a straggler older than the raw data would replace days of aggregate history with it.
- **Collector secrets:** `GROQ_API_KEY`, `DATABASE_URL` are env vars only — never committed. `.env.example` ships with empty values.
- **CORS:** `CORS_ORIGINS` lists the dashboard origins allowed to call the collector (unset: none; the docker-compose file sets `*` for local use). Allowed headers: `Content-Type`, `X-Reqly-Key`, `Authorization`.
- **No PII:** The SDKs capture method, route template, status, duration, error type, sizes, release and environment — never request bodies, query strings or headers. Consumer ids are optional and HMAC-SHA256-hashed with the app's salt inside the SDK, so API keys never reach the collector.

---

## 8. Error Handling & Reliability

| Failure | Behaviour |
|---|---|
| SDK internal error | Caught in `record_request()`, logged once at WARNING, SDK self-disables for that session — never propagates to host app |
| Collector unreachable | httpx timeout (connect 1s, read/write 2s); retried 3 times with exponential backoff + jitter; batch dropped and counter incremented after 3 failures |
| SDK queue full | Oldest event dropped, `dropped_events` counter incremented — request thread never blocks |
| Rate-limited (429), 408, or collector 5xx | SDK retries up to 3 times with backoff (safe: the collector dedups on `event_id`); other 4xx are dropped immediately |
| Groq API failure | Falls back to plain-text stats report; insight pipeline does not fail |
| DB write fails | asyncpg raises; collector returns 500 — SDK will retry the batch on next flush cycle |
| Malformed event in batch | Validated independently; one bad event increments `rejected` counter and is skipped; rest of batch is accepted |

---

## 9. Deployment

**Local:** `docker compose up -d` starts TimescaleDB, the collector, the dashboard and the load generator. The collector applies `collector/migrations/*.sql` on start-up; the load generator backfills weeks of history (as an explicit backfill), then sends live traffic.

**Live demo:**
1. TimescaleDB (`timescaledb-ha`, Toolkit, TLS) in Docker on an EC2 t3.micro, set up by `infra/ec2-userdata.sh`.
2. The collector on Render, `DATABASE_URL` with `sslmode=require`, `FORWARDED_ALLOW_IPS=*` so rate limits see real client IPs, `GROQ_API_KEY` for AI features, `PUBLIC_DASHBOARD` on.
3. The dashboard on Render, built with `VITE_COLLECTOR_URL` and `VITE_READ_KEY`.
4. Releases: PyPI (`sdk-v*` tags) and npm (`node-v*` tags) via trusted publishing; container images on GHCR (`collector-v*` tags).

**Optional:** an AWS SAM stack (`infra/sam`) can run the weekly report as a Lambda with EventBridge and an S3 archive; set `INSIGHTS_SCHEDULER_ENABLED=false` on the collector then. Step-by-step in [infra/DEPLOY.md](../infra/DEPLOY.md).

---

## 10. Explicit Scope Cuts

- **Trace views** — OTLP spans are reduced to request events; no span waterfall.
- **Request bodies, query strings, headers** — never captured.
- **Multiple collector instances** — scheduler, rate limits and caches are per process; run one collector.
- **OIDC / SSO** — username + password sign-in only.
- **Real-time push** — the dashboard polls; no WebSockets or SSE.

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
