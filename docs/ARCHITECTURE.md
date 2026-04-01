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
| AI | Groq API · Llama 3.3-70b-versatile · z-score anomaly detection (Python stdlib `statistics`) |
| Infra (local) | Docker Compose (4 services: timescaledb, collector, dashboard, load-generator) |
| Infra (prod) | EC2 t3.small (TimescaleDB) · AWS Lambda + EventBridge (weekly insights) · S3 (report archive) · SAM |

---

## 2. Components

```
reqly/
  sdk/                Python package (pip install reqly) — ASGI/WSGI middleware, buffer, shipper
  collector/          FastAPI service — ingest, metrics queries, insights scheduler
  collector/migrations/  001_init.sql — schema applied on TimescaleDB container boot
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

On startup the collector creates an asyncpg connection pool and starts an APScheduler job that runs the insights pipeline weekly (overrideable on demand). On shutdown it drains the scheduler and closes the pool.

### TimescaleDB

Raw events stored in a `request_events` hypertable (1-day chunks, composite PK `(time, event_id)`, 14-day retention). Three continuous aggregates pre-compute rollups on insert:

- `route_latency_1min` — p50/p95/p99/avg per minute (90-day retention)
- `route_errors_1hour` — error count, error rate, p95 per hour (180-day retention)
- `route_status_distribution_1hour` — status code counts per hour (180-day retention)

A separate plain `insight_reports` table stores one row per service per week (not a hypertable — TimescaleDB features add nothing for low-cardinality weekly data).

### Dashboard (`dashboard/src/`)

Single-page React app built with Vite. State is TanStack Query — metrics are polled every 30 s by default. Components: `ServiceSelector`, `TimeRangePicker`, `LatencyChart` (Recharts LineChart with p50/p95/p99 series), `ErrorRateChart`, `StatusDistributionChart` (pie), `TopRoutesTable`, `InsightsPanel`. KPI tiles show live requests/min, p95, and error rate. The dashboard is a static SPA — the Vite build is deployed to any static host; it calls the collector directly from the browser.

### AI Insights Pipeline

1. APScheduler triggers weekly (or on-demand via API endpoint).
2. Pulls 8 weeks of hourly aggregates from `route_errors_1hour`.
3. `anomaly_detection.py` computes a day-of-week × hour-of-day seasonal baseline from the older 7 weeks, compares the most recent 7 days, flags any (route, dow, hour) cell with z-score > 2.0 (requires ≥ 3 baseline samples to avoid false positives from thin data).
4. Top 5 anomalies by z-score are serialized to JSON.
5. If `GROQ_API_KEY` is set, the structured anomaly JSON is sent to Llama 3.3-70b-versatile (temperature 0.3, max 600 tokens) with a system prompt that explicitly forbids inventing root causes. Otherwise the raw statistical findings are formatted as plain text.
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
| `GET` | `/v1/services` | Read key | List all service names with recorded traffic |
| `GET` | `/v1/services/{service_name}/routes` | Read key | List all route templates for a service |
| `GET` | `/v1/metrics/summary` | Read key | Latency series, error rate series, status distribution, top routes, requests/min for a service+window |
| `GET` | `/v1/insights/latest` | Read key | Latest weekly AI report for a service |
| `POST` | `/v1/insights/generate` | Read key | Trigger insights generation on demand (rate-limited 5/min) |

---

## 7. Security

- **API keys:** `REQLY_INGEST_KEY` (write) and `REQLY_READ_KEY` (read) are separate secrets — sharing read access with a dashboard doesn't expose ingest credentials. Both are passed via `X-Reqly-Key` header. Defaults to `demo-key` with a startup warning if not set.
- **Ingest validation:** Every event in a batch is validated with Pydantic before writing. `duration_ms` is clamped (0–300 000 ms). Batch size is hard-capped at 1 000 events per call.
- **Rate limiting:** slowapi enforces 600 req/min on ingest; insights generation is separately limited at 5/min.
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
| Rate-limited (429) | SDK retries up to 3 times with backoff |
| Groq API failure | Falls back to plain-text stats report; insight pipeline does not fail |
| DB write fails | asyncpg raises; collector returns 500 — SDK will retry the batch on next flush cycle |
| Malformed event in batch | Validated independently; one bad event increments `rejected` counter and is skipped; rest of batch is accepted |

---

## 9. Deployment

**Local (Docker Compose):**
1. `docker compose up -d` starts all 4 services (timescaledb → collector → dashboard → load-generator).
2. Schema applied automatically from `collector/migrations/001_init.sql` via `docker-entrypoint-initdb.d`.
3. Load generator backfills synthetic history on first run, then generates live traffic at ~2 RPS.

**Production (AWS):**
1. EC2 t3.small runs TimescaleDB in Docker; user-data script applies schema on boot.
2. Collector deployed as Docker container on EC2 (or Render/Fly.io) with env vars set.
3. Dashboard built (`npm run build`) and deployed to any static host (Vercel, S3+CloudFront, Render).
4. AWS SAM stack deploys `reqly-weekly-insights` Lambda + EventBridge (Sunday 23:00 UTC) + S3 archive for report JSON. Estimated cost: ~$17/month (EC2 t3.small + EBS 20 GB; Lambda/S3 on free tier).

---

## 10. Explicit Scope Cuts

- **Distributed tracing (spans/traces)** — Reqly captures request-level metrics only, not inter-service traces. OpenTelemetry integration is a v2 candidate.
- **Alerting / PagerDuty integration** — the insights report surfaces anomalies but does not fire alerts. Would require webhook config and a notification layer.
- **Multi-tenant / per-user isolation** — single shared collector; service_name is the only isolation boundary. Per-tenant key management deferred to v2.
- **Real-time streaming (WebSockets/SSE)** — dashboard polls every 30 s. Real-time push would require a WebSocket server or SSE endpoint on the collector.
- **Non-Python SDKs** — only FastAPI and Flask (Python). Node.js, Go, etc. are v2 candidates.
