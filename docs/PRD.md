# Reqly — Product Requirements Document

**Status:** Final (v1)
**Owner:** Tanish Poddar
**One-liner:** Self-hostable API observability SDK for FastAPI and Flask — auto-instrument with 2 lines, get real-time metrics and weekly AI anomaly reports.

---

## 1. Problem

Developers running Python microservices (FastAPI, Flask) have no lightweight, self-hostable way to answer basic reliability questions: which routes are slow, which are failing, and are things getting worse? The mainstream alternatives (Datadog, New Relic) require a 50 MB agent, per-host billing, and weeks of integration work. The result is most small and medium services fly blind until something breaks in production and the only diagnostic is application logs.

---

## 2. Goals (v1 / MVP)

1. Two-line instrumentation: `import reqly; reqly.instrument(app, service_name="my-api")` is the entire SDK integration.
2. Real-time metrics dashboard: p50/p95/p99 latency per route, error rates, status code distribution, top routes, live requests/min — all accessible within minutes of adding the SDK.
3. Weekly AI anomaly report: z-score detection over a seasonal baseline, with Groq writing a plain-English narrative about what degraded and when.
4. Self-hostable full stack: entire system runs with `docker compose up` — no external SaaS dependency required.
5. Fail-open guarantee: Reqly must never crash or slow down the instrumented application. Any internal failure silently self-disables.
6. Published to PyPI and live demo deployed with real traffic.

---

## 3. Non-Goals (explicit scope cuts)

- **Distributed tracing** — Reqly captures request-level metrics, not inter-service spans or traces. OpenTelemetry integration is a v2 candidate once the metrics story is solid.
- **Alerting / notifications** — the weekly report surfaces anomalies but does not fire PagerDuty/Slack alerts. Operational alerting requires SLA commitments around false-positive rates that are out of scope for v1.
- **Non-Python SDKs** — FastAPI and Flask cover the primary Python microservice stack. Node.js/Go SDKs are v2 once the collector protocol is stable.
- **Multi-tenant SaaS** — v1 is self-hosted, single-tenant. Per-customer key management and billing add significant complexity without clear v1 benefit.
- **Real-time WebSocket streaming** — polling every 30 s is sufficient for the metrics use case and avoids a persistent connection layer on the collector.
- **Request body / PII capture** — the SDK captures only route, method, status, duration, error type. No query params, headers, or bodies. Explicitly out of scope due to data privacy concerns.

---

## 4. Users

**Primary:** Backend developers running FastAPI or Flask microservices who want lightweight observability without a managed SaaS agent — either for cost, data sovereignty, or simplicity reasons.

**Secondary:** Recruiters and technical interviewers evaluating this as a portfolio piece — it must be demonstrable live with real traffic from the EventFlow demo app.

---

## 5. User Stories

1. *As a developer,* I can add `reqly.instrument(app, service_name="checkout-api")` to my existing FastAPI app and immediately start collecting metrics without touching any other code.
2. *As a developer,* I can open the Reqly dashboard, select my service, and see which routes have the highest p95 latency and error rate in the last hour — without writing any SQL.
3. *As a developer,* I can switch the time window to 7d and see whether the slowdown I noticed today is new or has been building for a week.
4. *As a developer,* I can read the weekly AI insights report and see a plain-English explanation of which route degraded, at what time of week, compared to its historical baseline.
5. *As a developer,* I can self-host the full stack by running `docker compose up` and pointing my app at `http://localhost:8000`.
6. *As a developer,* I can deploy the collector to production (EC2 + AWS Lambda) using the provided SAM template and deployment guide.
7. *As a recruiter,* I can visit the live demo URL, see real metrics from the EventFlow app, and understand what the tool does within 30 seconds.

---

## 6. Functional Requirements

### 6.1 SDK

- Supports FastAPI (pure ASGI middleware) and Flask (before/after request hooks).
- `reqly.instrument(app)` auto-detects framework — no separate `instrument_fastapi` / `instrument_flask` calls required.
- Captures: HTTP method, matched route template, status code, duration (ms), error flag, error type (exception class name).
- Route normalization: uses framework's matched template (`/users/{id}`), not raw path. Unmatched paths collapse to `__unmatched__`.
- Configurable: service name, collector URL, API key, sample rate, flush interval, batch size, queue size, ignore routes list.
- Must never raise an exception into the host application — all SDK errors caught internally.
- Background flush thread is a daemon thread; bounded queue (default 2 000 events); ships batches of ≤ 200 events every 5 s.
- Outbound HTTP calls use strict timeouts (connect 1 s, read 2 s) so a slow collector never blocks requests.

### 6.2 Collector (Ingest)

- `POST /v1/ingest`: authenticates via `X-Reqly-Key`, accepts batches of ≤ 1 000 events, validates each event independently (partial-batch acceptance), writes to TimescaleDB.
- Rate-limited at 600 req/min (configurable) to protect the DB under load.
- Returns `{"accepted": N, "rejected": M}` so the SDK can track data quality.

### 6.3 Collector (Metrics)

- `GET /v1/metrics/summary`: returns latency series, error rate series, status distribution, top routes (by volume and error rate), and current requests/min — for a given service name and time window (1h/6h/24h/7d).
- Optional route filter for per-route drill-down.
- Reads from continuous aggregates, not raw event table, for query performance.
- Separate read key from ingest key — sharing dashboard access doesn't expose write credentials.

### 6.4 AI Insights

- Weekly pipeline: z-score anomaly detection over 8 weeks of hourly data, grouped by day-of-week × hour-of-day.
- Anomaly threshold: z-score > 2.0, minimum 3 baseline samples required (to avoid false positives on thin data).
- Top 5 anomalies by z-score sent to Groq (Llama 3.3-70b-versatile).
- If no Groq API key: plain-text statistical findings shown instead (not an error).
- On-demand trigger via `POST /v1/insights/generate` (rate-limited at 5/min for demo safety).
- Results stored in `insight_reports` table; served via `GET /v1/insights/latest`.

### 6.5 Dashboard

- Service selector: lists all services with recorded traffic.
- Time window picker: 1h / 6h / 24h / 7d.
- Optional route filter: drill down into a single route template.
- KPI tiles: requests/min, p95 latency (ms), error rate (%) with green/red color based on threshold.
- Charts: latency (p50/p95/p99 over time), error rate over time, status code distribution (pie), top routes table.
- Insights panel: renders latest AI report markdown.
- Responsive layout. Dark-themed.

---

## 7. Non-Functional Requirements

- **SDK overhead:** capturing and buffering a request event must add < 1 ms to response time (background flush, no inline I/O).
- **Ingest latency:** `POST /v1/ingest` ack < 500 ms for a typical 200-event batch.
- **Dashboard load:** metrics summary query < 2 s for any supported time window (continuous aggregates pre-compute the heavy aggregation).
- **Security:** API keys in env vars only — never committed. No PII in the telemetry schema. Ingest and read keys are separate secrets.
- **Cost:** AWS production stack at ~$17/month (EC2 t3.small + EBS 20 GB; Lambda/EventBridge/S3 on free tier).
- **Reliability:** SDK must be fail-open — no SDK failure can propagate to the host app or cause request failures.

---

## 8. Success Metrics

| Metric | Target |
|---|---|
| Live demo reliability | Dashboard loads with real data within 5 s on first visit |
| PyPI publish | `pip install reqly` installs successfully on Python 3.9–3.12 |
| SDK integration friction | Working metrics from a new FastAPI app in < 5 minutes |
| AI report quality | Report references specific routes and time patterns, not generic advice |

---

## 9. Risks & Open Questions

- **TimescaleDB continuous aggregate lag:** continuous aggregate policies have a 1-minute end_offset — the last minute of data is not yet aggregated. Dashboard queries fall back to raw table for the most recent window. Acceptable for v1; time-series freshness vs. query cost tradeoff.
- **Groq rate limits:** free-tier Groq has token/minute caps. The weekly batch job is well within limits, but on-demand generation triggered frequently by demo visitors could hit them. Mitigated by the 5/min rate limit on `POST /v1/insights/generate`.
- **Open question:** should the anomaly threshold (z > 2.0) be configurable per service, or is a global default sufficient for v1?

---

## 10. v2 Candidates

- **OpenTelemetry integration** — export spans to OTLP alongside (or instead of) the proprietary ingest format; interoperability with Jaeger/Tempo.
- **Alerting webhooks** — fire to Slack/PagerDuty when z-score exceeds a configurable threshold; requires false-positive tuning.
- **Node.js and Go SDKs** — expand beyond Python; would require stabilizing the collector ingest protocol as a public spec.
- **Multi-tenant support** — per-customer API keys, isolated data views, usage metering; prerequisite for a hosted SaaS offering.
- **GitHub Check Run integration** — block a merge if error rate on a canary deployment exceeds baseline (requires real-time anomaly detection, not just weekly batch).
