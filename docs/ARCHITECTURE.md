# Reqly — Architecture

<!--
Companion to PRD.md.
PRD says WHAT the system does. This says HOW.
Audience: an engineer who needs to understand the system well
enough to build it, debug it, or extend it.
-->

---

## 1. Stack

| Layer | Tech |
|---|---|
| SDK | Python 3.9+ · threading · httpx 0.27 · pure ASGI middleware (FastAPI) · WSGI hooks (Flask) |
| Collector | FastAPI 0.110 · Uvicorn · asyncpg 0.29 · APScheduler 3.x · slowapi · Pydantic v2 · Mangum (Lambda adapter) |
| Database | TimescaleDB latest-pg16 · hypertables · continuous aggregates · retention policies |
| Dashboard | React 19 · Vite 8 · TypeScript 6 · Tailwind CSS 4 · Recharts 3 · TanStack Query 5 |
| AI | Groq API · gpt-oss-120b · z-score anomaly detection (Python stdlib `statistics`) |
| Infra (local) | Docker Compose (4 services: timescaledb, collector, dashboard, load-generator) |
| Infra (prod) | EC2 t3.small (TimescaleDB) · AWS Lambda + EventBridge (weekly insights) · S3 (report archive) · SAM |

---

## 2. Components

```
reqly/
  sdk/                Python package (pip install reqly) — ASGI/WSGI middleware, buffer, shipper
  collector/          FastAPI service — ingest, metrics queries, insights scheduler
  collector/migrations/  001_init.sql — schema applied by the collector on startup
  dashboard/          React SPA — charts, KPI tiles, insights panel
  load_generator/     Synthetic traffic generator for demo/backfill
  demo/               Flask EventFlow app — the live demo target instrumented by the SDK
  infra/              AWS SAM template, EC2 user-data script, deployment docs
```

### SDK (`sdk/reqly/`)

Auto-instruments FastAPI (pure ASGI middleware wrapping `send`) and Flask (before/after request hooks). The SDK is strictly non-blocking: `record_request()` puts an event onto an in-memory `deque(maxlen=2000)` (dropping oldest on backpressure) and returns immediately. A single daemon background thread flushes batches of up to 200 events every 5 seconds via `httpx.Client` with strict per-phase timeouts (connect 1s, read 2s, write 2s). On any internal failure the SDK logs once at WARNING and self-disables — it never raises into the host application. Route normalization uses the framework's matched template (`/users/{id}`) so cardinality is O(routes), not O(URLs); unmatched paths collapse to `__unmatched__`.

### Collector (`collector/app/`)

FastAPI service with three routers:

- **Ingest** (`POST /v1/ingest`) — receives SDK batches, validates each event independently (partial-batch acceptance: one bad event drops only itself), authenticates via `X-Reqly-Key`, rate-limited at 600 req/min via slowapi, writes to TimescaleDB with asyncpg.
- **Metrics** (`GET /v1/metrics/summary`, `/v1/services`, `/v1/services/{name}/routes`) — reads from continuous aggregates using concurrent `asyncio.gather` for the five sub-queries. All reads require `X-Reqly-Key` (read key, separate from ingest key in production).
- **Insights** (`GET /v1/insights/latest`, `POST /v1/insights/generate`) — serves the latest weekly report, or triggers one on demand for demos.

On startup the collector creates an asyncpg connection pool, starts a late-data refresher (events older than the aggregates' 1h policy look-back — backfills, SDK retries — are materialized explicitly via `refresh_continuous_aggregate`, in 7-day slices), and starts an APScheduler job that runs the insights pipeline weekly (overrideable on demand). On shutdown it drains the scheduler and closes the pool.

### TimescaleDB

Raw events stored in a `request_events` hypertable (1-day chunks, composite PK `(time, event_id)`, 14-day retention). Three continuous aggregates pre-compute rollups on insert:

- `route_latency_1min` — p50/p95/p99/avg per minute (90-day retention)
- `route_errors_1hour` — error count, error rate, p95 per hour (180-day retention)
- `route_status_distribution_1hour` — status code counts per hour (180-day retention)

A separate plain `insight_reports` table stores one row per service per week (not a hypertable — TimescaleDB features add nothing for low-cardinality weekly data).

### Dashboard (`dashboard/src/`)

Single-page React app built with Vite. State is TanStack Query — metrics are polled every 30 s by default. Components: `ServiceSelector`, `TimeRangePicker`, `LatencyChart` (Recharts LineChart with p50/p95/p99 series), `ErrorRateChart`, `StatusDistributionChart` (pie), `TopRoutesTable`, `InsightsPanel`. KPI tiles show live requests/min, p95, and error rate. The dashboard is a static SPA — the Vite build is deployed to any static host; it calls the collector directly from the browser.

### Hourly alerts

At :15 past every hour the collector refreshes the last completed hour in `route_errors_1hour` and compares it with the same weekday-hour in the previous 8 weeks using the same detector as the weekly report (`recent_window=1h`). Anomalies get the same release context and hints, then `app/alerts/hourly.py` keeps at most one open alert per (service, route) in the `alerts` table: a new detection opens one and notifies, repeats update it (reminder every `ALERT_RENOTIFY_HOURS`), and two clean hours resolve it with a final notification. Messages go to Slack, Discord and/or a generic JSON webhook, built from the statistics only (no LLM in the alert path). Run the scheduler on a single collector instance.

### SLOs and error budgets

An SLO is one objective for a service or route over a window (default 28 days): `availability` (share of requests without an error) or `latency` (share at or under a threshold). The window's SLI and error budget come from `api_latency_1min` (latency via `approx_percentile_rank` on the merged sketch; without the Toolkit, from raw events covering at most 14 days). Burn rates for 5m / 30m / 1h / 6h come from raw events, which are current to the second. Every 5 minutes `app/alerts/slo_alerts.py` applies the multi-window burn-rate rules from the Google SRE workbook — fast burn when the 1h **and** 5m burn rates are ≥ 14.4, slow burn when the 6h **and** 30m rates are ≥ 6, with at least 20 requests in the longer window — and keeps one open alert per SLO (`kind = 'slo'` in `alerts`), notified through the same channels as anomaly alerts and resolved after 30 minutes out of burn.

### Ask Reqly

`POST /v1/ask {service_name, question}` answers questions like "why was /orders slow yesterday afternoon?" with Groq tool calling (`app/ask/`). The model never writes SQL: it chooses among six read-only tools — `get_stats` (totals or per route/hour/day, from the sketch aggregate), `compare_periods`, `get_breakdown` (by host, environment, release, status code, error type or method, from raw events), `list_releases`, `get_alerts` and `get_slos` — whose arguments are validated and whose time ranges are clamped to retention. Results are rounded aggregates, never raw events. The system prompt carries the current UTC time, the dates of the last 8 days, the service's routes and its recent releases, so relative times ("last Monday") and deploys resolve without a lookup. At most 6 tool calls per question; after that the model must answer from what it has. The response includes every tool call and its result, and the dashboard shows them under the answer. Every number in the answer is then looked up in those results (as written, as a percentage, ms as seconds, a ratio as a percent change, at the written precision); the ones not found come back as `unverified_numbers` and the dashboard flags them, since models occasionally mis-copy digits. The read key is public to dashboard viewers, so the endpoint is limited to 5 questions a minute per IP and `ASK_DAILY_LIMIT` (default 200) per collector per day. An eval set over the load generator's scenarios lives in `collector/tests/ask_eval/` (run by hand; it needs a Groq key).

### AI Insights Pipeline

1. APScheduler triggers weekly (or on-demand via API endpoint).
2. Pulls 8 weeks of hourly aggregates from `route_errors_1hour`.
3. `anomaly_detection.py` computes a day-of-week × hour-of-day seasonal baseline from the older 7 weeks, compares the most recent 7 days, and flags (route, dow, hour) cells whose error count or p95 rose significantly. Error rates use an exact Poisson tail test on the request/error counts; p95 is only tested on hours with ≥ 100 requests. A cell is flagged at z > 4.0 (≈ Bonferroni for ~1,700 cells per run) and only if the shift is material (≥ 2pp of errors or ≥ 50% p95). Requires ≥ 3 baseline samples.
4. Top 5 anomalies by z-score are serialized to JSON, each with the hour it happened (`window_start`).
4c. `hints.py` adds deterministic root-cause `hints`: errors or slow requests concentrated on one host/environment relative to its traffic share, or an error type / status code that dominates the errors and was rare in the previous week.
4b. `deploys.py` adds a `release_context` to each anomaly from raw events + the `deployments` table: which release served that route in that hour, whether it was first seen within the week before (so the baseline ran on something else), and for a new release the previous release plus before/after error rate and p95 on that route.
5. If `GROQ_API_KEY` is set, the structured anomaly JSON is sent to `openai/gpt-oss-120b` (temperature 0.3, at most 2 000 tokens including reasoning) with a system prompt that explicitly forbids inventing root causes. Otherwise the raw statistical findings are formatted as plain text.
6. The result is upserted into `insight_reports`.

---

## 3. Data Flow

```
[Your App (FastAPI/Flask)]
    │ ASGI/WSGI middleware wraps every request
    │ route template + status + duration_ms captured in finalizer
    └─► [SDK EventBuffer (deque, maxlen=2000)]
            │ background daemon thread flushes every 5 s
            └─► POST /v1/ingest  (X-Reqly-Key, batch ≤ 200 events)
                    │
            [Collector — FastAPI]
                    │ partial-batch validation (Pydantic EventIn)
                    └─► asyncpg INSERT INTO request_events
                                │
                        [TimescaleDB hypertable]
                                │ continuous aggregate policies run on insert
                                ├─► route_latency_1min (every 1 min)
                                ├─► route_errors_1hour (every 1 hour)
                                └─► route_status_distribution_1hour (every 1 hour)

[Browser Dashboard]
    │ TanStack Query polls every 30 s
    └─► GET /v1/metrics/summary?service_name=...&window=1h
            │ asyncio.gather (5 concurrent queries against continuous aggregates)
            └─► JSON response → Recharts / KPI tiles

[APScheduler — weekly]
    └─► GET 8 weeks route_errors_1hour
            │ z-score anomaly detection (stdlib statistics)
            └─► POST Groq API (structured anomaly JSON)
                    └─► UPSERT insight_reports
                            └─► GET /v1/insights/latest → InsightsPanel
```

1. Every HTTP request in the instrumented app is captured by the SDK middleware after the response sends.
2. Events are buffered in-process and shipped in batches to the collector every 5 seconds.
3. The collector validates each event independently, writes accepted rows to the `request_events` hypertable.
4. TimescaleDB continuous aggregate policies roll up the raw events into per-minute and per-hour materialized views.
5. The dashboard polls `GET /v1/metrics/summary`, which reads from the pre-computed aggregates — no full-table scans.
6. Weekly, the insights pipeline reads 8 weeks of hourly data, runs z-score detection, and calls Groq to write a narrative report stored in `insight_reports`.
7. The dashboard's `InsightsPanel` renders the latest report on demand.

---

## 4. Database Schema

**Latency percentiles (migration 003).** With the TimescaleDB Toolkit installed (`timescaledb-ha` image, Timescale Cloud), `api_latency_1min` stores a `uddsketch(1000, 0.005)` per minute, service, environment, route and method. Sketches merge, so service-level and multi-hour p50/p95/p99 are real percentiles of all requests (within ~0.2%) instead of the max of per-route percentiles, long windows are re-bucketed (6h → 5 min, 24h → 15 min, 7d → 1 h), and error rates come from the same 1-minute aggregate instead of the hourly one that lags by up to two hours. Without the Toolkit the migration is a no-op and the collector keeps using the `percentile_cont` views.

The ingest contract (fields, limits, retry semantics) is specified in [INGEST_SPEC.md](INGEST_SPEC.md). Schema v2 (migration `002_event_v2.sql`) adds optional `release`, `environment`, `consumer_id`, byte counts and LLM token columns to `request_events`, plus a `deployments` table (first/last seen per service, environment and release) that ingest maintains for deploy-aware insights.

- `request_events` — hypertable; `event_id UUID`, `time TIMESTAMPTZ`, `service_name TEXT`, `method TEXT`, `route TEXT`, `status_code SMALLINT`, `duration_ms DOUBLE PRECISION`, `is_error BOOLEAN`, `error_type TEXT`, `host TEXT`. Partitioned daily. 14-day retention.
- `route_latency_1min` — continuous aggregate; `bucket`, `service_name`, `route`, `request_count`, `p50_ms`, `p95_ms`, `p99_ms`, `avg_ms`. 90-day retention.
- `route_errors_1hour` — continuous aggregate; `bucket`, `service_name`, `route`, `request_count`, `error_count`, `error_rate`, `p95_ms`. 180-day retention.
- `route_status_distribution_1hour` — continuous aggregate; `bucket`, `service_name`, `route`, `status_code`, `count`. 180-day retention.
- `insight_reports` — plain table (not hypertable); `id UUID`, `service_name TEXT`, `week_start DATE`, `anomalies_json JSONB`, `report_text TEXT`, `generated_at TIMESTAMPTZ`. Unique on `(service_name, week_start)`.

**Indexes:**
- `idx_request_events_service_route_time` on `request_events(service_name, route, time DESC)` — serves per-route metric queries
- `idx_request_events_errors` on `request_events(service_name, time DESC) WHERE is_error` — partial index for error-only scans

---

## 5. AI / LLM Design

### Input

Structured JSON of pre-computed anomaly objects — never raw events or diffs. Each anomaly includes: `route`, `day_of_week`, `hour_range`, `observed_error_rate`, `baseline_error_rate`, `observed_p95_ms`, `baseline_p95_ms`, `z_score`.

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
| `POST` | `/v1/ingest` | Ingest key | Batch ingest of request events (≤ 1 000 per call); partial-batch acceptance |
| `POST` | `/otlp/v1/traces` | Ingest key | OTLP/HTTP trace receiver (protobuf or JSON, gzip) — HTTP server spans become request events; see [OTEL.md](OTEL.md) |
| `GET` | `/v1/services` | Read key | List all service names with recorded traffic |
| `GET` | `/v1/services/{service_name}/routes` | Read key | List all route templates for a service |
| `GET` | `/v1/metrics/summary` | Read key | Latency series, error rate series, status distribution, top routes, requests/min for a service+window |
| `GET` | `/v1/services/{service_name}/releases` | Read key | Recent releases with first/last seen, request volume, error rate and p95 (14-day raw window) |
| `GET` | `/v1/insights/latest` | Read key | Latest weekly AI report for a service |
| `GET` | `/v1/alerts` | Read key | Open (or recent, `status=all`) alerts — hourly anomalies and SLO burn — optionally per service |
| `GET` | `/v1/slos` | Read key | SLOs with live status: SLI, error budget left, burn rates (5m / 30m / 1h / 6h), state |
| `PUT` | `/v1/slos` | Ingest key | Create or update an SLO (by service + name) |
| `DELETE` | `/v1/slos/{id}` | Ingest key | Delete an SLO |
| `POST` | `/v1/ask` | Read key | Ask Reqly: answers a question about one service from its data (5/min per IP, `ASK_DAILY_LIMIT` per day) |
| `POST` | `/v1/insights/generate` | Read key | Trigger insights generation on demand (rate-limited 5/min) |

---

## 7. Security

- **API keys:** `REQLY_INGEST_KEY` (write) and `REQLY_READ_KEY` (read) are separate keys, passed via the `X-Reqly-Key` header and compared in constant time. The read key is compiled into the dashboard bundle, so it is effectively public to dashboard viewers; it never falls back to the ingest key, and the collector warns at startup if the two are equal. Defaults: `demo-key` / `demo-read-key`, with a startup warning.
- **Ingest validation:** Every event in a batch is validated with Pydantic before writing. `duration_ms` is clamped (0–300 000 ms). Batch size is hard-capped at 1 000 events per call, and string fields are length-capped (`service_name` 128, `route` 512, `method` 16).
- **Rate limiting:** slowapi enforces 600 req/min per client IP on ingest; insights generation is separately limited at 5/min. Behind a proxy, set `FORWARDED_ALLOW_IPS` so the real client IP is used.
- **Collector secrets:** `GROQ_API_KEY`, `DATABASE_URL` are env vars only — never committed. `.env.example` ships with empty values.
- **CORS:** Configured via `CORS_ORIGINS` env var — defaults to `*` for local dev, should be restricted in production.
- **No PII:** The SDK captures only method, route template, status code, duration, and error type — no request bodies, no query params, no user identifiers by default.

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

**Local (Docker Compose):**
1. `docker compose up -d` starts all 4 services (timescaledb → collector → dashboard → load-generator).
2. The collector applies `collector/migrations/*.sql` on startup, tracking applied files in `schema_migrations`.
3. Load generator backfills synthetic history on first run, then generates live traffic at ~2 RPS.

**Production (AWS):**
1. EC2 t3.small runs TimescaleDB in Docker (user-data script); the collector applies the schema on first start.
2. Collector deployed as Docker container on EC2 (or Render/Fly.io) with env vars set.
3. Dashboard built (`npm run build`) and deployed to any static host (Vercel, S3+CloudFront, Render).
4. AWS SAM stack deploys `reqly-weekly-insights` Lambda (set `INSIGHTS_SCHEDULER_ENABLED=false` on the collector so the job doesn't run twice) + EventBridge (Sunday 23:00 UTC) + S3 archive for report JSON. Estimated cost: ~$17/month (EC2 t3.small + EBS 20 GB; Lambda/S3 on free tier).

---

## 10. Explicit Scope Cuts

- **Distributed tracing (spans/traces)** — Reqly captures request-level metrics only, not inter-service traces. OpenTelemetry integration is a v2 candidate.
- **Alerting / PagerDuty integration** — the insights report surfaces anomalies but does not fire alerts. Would require webhook config and a notification layer.
- **Multi-tenant / per-user isolation** — single shared collector; service_name is the only isolation boundary. Per-tenant key management deferred to v2.
- **Real-time streaming (WebSockets/SSE)** — dashboard polls every 30 s. Real-time push would require a WebSocket server or SSE endpoint on the collector.
- **Non-Python SDKs** — only FastAPI and Flask (Python). Node.js, Go, etc. are v2 candidates.
