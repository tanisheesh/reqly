# Reqly — Product Requirements Document

**Status:** Final (v2 — shipped; collector 0.10, Python SDK 0.5, Node SDK 0.1)
**Owner:** Tanish Poddar
**One-liner:** Self-hosted API monitoring that tells you what broke, when, and which deploy did it — for Python, Node.js and any OpenTelemetry stack.

---

## 1. Problem

Teams running a handful of HTTP APIs have two bad options for knowing whether those APIs are healthy. Hosted APMs (Datadog, New Relic) are priced per host and ship the data off to a vendor; lightweight self-hosted tools show charts but leave the work of explaining them to whoever is on call. When `/orders` starts failing at 3 pm, the questions are always the same — did a deploy do this, is it one host or one client, is it worse than a normal Tuesday, how much of our reliability budget is gone — and answering them means stitching together logs, deploy history and dashboards by hand.

---

## 2. Goals

1. One-line instrumentation for Python (FastAPI, Flask, Django, Starlette, Litestar, any WSGI/ASGI app) and Node.js (Express, Fastify, Hono); any other language through OpenTelemetry (OTLP/HTTP) with exporter settings only.
2. Correct latency percentiles at every level (route, service, 1h to 7d) and error rates that are current to the minute.
3. Explanations, not just charts: every alert carries the release that was running, root-cause leads (host, environment, error type, status code) and the API consumers it hit.
4. Hourly anomaly alerts against a weekday × hour baseline, deduplicated and auto-resolved, delivered to Slack, Discord or a webhook; a weekly AI-written report.
5. Questions in plain English (Ask Reqly) answered only from the collector's own data, with the queries behind every answer visible.
6. API-level views: SLOs and error budgets, OpenAPI drift, consumers, LLM cost per route.
7. Self-hostable with `docker compose up`; a shared collector can serve several teams with projects, scoped API keys and sign-in.
8. The SDKs are fail-open and their per-request overhead is measured and published.

---

## 3. Non-Goals (explicit scope cuts)

- **Trace views / distributed tracing UI** — Reqly ingests OTLP spans but keeps only HTTP server spans as request events; there is no span waterfall. Request-level metrics are the product.
- **Logs** — Reqly stores no log lines; it links to nothing outside its own data.
- **Request bodies, query strings, headers** — never captured. Consumer ids are hashed in the SDK; the collector never sees API keys.
- **Hosted SaaS and billing** — Reqly is self-hosted; projects and keys exist for teams sharing one collector, not for customers.
- **SSO / OIDC sign-in** — username and password only; OIDC would come when a real deployment needs it.
- **Horizontal scaling of the collector** — the alert scheduler and rate limits assume one collector instance (state is in Postgres, but jobs and limits are per process).
- **Real-time push to the dashboard** — polling every 30–60 s is enough for this use case.

---

## 4. Users

**Primary:** Backend developers and small platform teams running a few HTTP services who want to know quickly why an endpoint degraded, on infrastructure they control.

**Secondary:** Recruiters and interviewers evaluating Reqly as a portfolio project — the live demo must show real traffic and real alerts without setup.

---

## 5. User Stories

1. *As a developer,* I add `reqly.instrument(app)` (or one middleware line in Node) and see per-route latency, errors and status codes within a minute, so that I don't need an agent or a vendor account.
2. *As an on-call engineer,* I get a Slack alert saying `/orders` errors are at 30% against a 2% norm since release `v2`, with 92% of errors on one pod and the clients affected, so that I know where to look before opening a dashboard.
3. *As a developer,* I ask "why was checkout slow yesterday afternoon?" and get an answer with the numbers and the queries behind it, so that I can verify it rather than trust it.
4. *As a team lead,* I define a 99.5% availability SLO on `/checkout` and get an alert when the error budget burns too fast, so that we react to user impact, not to every blip.
5. *As an API owner,* I upload our OpenAPI spec and see which endpoints get traffic without being documented, which documented ones nobody calls, and which clients still use deprecated ones, so that I can clean up the API safely.
6. *As a developer of an LLM feature,* I record token usage per request and see cost per route and per 1k requests, so that I know which endpoint is expensive.
7. *As an admin of a shared collector,* I create a project per team with its own keys and members, so that teams only see and write their own services.
8. *As a Node, Go or Java developer,* I point my OpenTelemetry exporter at Reqly and get the same per-route metrics without installing a Reqly SDK.

---

## 6. Functional Requirements

### 6.1 SDKs

- Python: auto-detects FastAPI, Starlette, Litestar and Flask from `reqly.instrument(app)`; Django via middleware; any WSGI/ASGI app via `instrument_wsgi` / `instrument_asgi` with a route resolver.
- Node.js: `reqlyExpress`, `reqlyFastify`, `reqlyHono` middleware; ESM and CommonJS builds.
- Records method, route template (never the raw path; unmatched requests become `__unmatched__`), status, duration, error type, request/response size, release (auto-detected from CI variables), environment, optional consumer id (HMAC-hashed with the app's salt) and optional LLM token usage.
- Never raises into the host app; bounded queue; batches shipped in the background with retries on 408/429/5xx.
- FastAPI and Litestar apps can upload their own OpenAPI spec (`push_openapi=True`).

### 6.2 Ingest

- `POST /v1/ingest` validates each event independently (one bad event drops only itself) and reports reasons for rejections.
- `POST /otlp/v1/traces` accepts OTLP/HTTP protobuf or JSON (gzip), maps HTTP server spans to events (stable and legacy semantic conventions).
- Events older than 13 days are refused unless the batch is an explicit backfill; events more than 15 minutes in the future are refused.
- A new service joins the project of the key that sends its first events; another project's keys can't write to it.

### 6.3 Metrics and dashboard

- Service and route selection, 1h / 6h / 24h / 7d windows; latency (p50/p95/p99), error rate, status codes, top routes, requests/min, release markers and a releases table.
- Panels that appear when their data exists: alerts, SLOs, consumers, LLM cost, API surface (drift), Ask Reqly, weekly insights.
- Sign-in page when the dashboard isn't public; settings for password, projects, services, API keys and members.

### 6.4 Detection and alerts

- Hourly check of the last complete hour of every route against the same weekday-hour in the previous 8 weeks; error rates tested with a Poisson tail on counts, p95 only on hours with ≥ 100 requests, z ≥ 4 with minimum effect sizes.
- Each alert includes release context (new release, before/after numbers), root-cause hints and affected consumers; one open alert per route, reminders every 6 h, resolved after 2 clean hours.
- Weekly report over the whole week with the same statistics, narrated by an LLM (plain-text fallback without a key).

### 6.5 AI

- Ask Reqly answers one service's questions with nine read-only tools (stats, period comparison, breakdowns, releases, alerts, SLOs, drift, consumers, LLM cost), at most 6 tool calls, numbers in the answer verified against the tool results.
- An eval set (19 questions over the demo scenarios) is run by hand after prompt or model changes.

### 6.6 API depth

- SLOs: availability or latency objectives per service or route over 1–90 days; SLI, budget remaining, burn rates (5m/30m/1h/6h); fast/slow burn alerts (Google SRE workbook rules).
- OpenAPI drift: spec upload (OpenAPI 3 / Swagger 2, JSON or YAML) compared with 30 days of traffic.
- Consumers: top consumers, one consumer's routes, who an incident hit, who still calls deprecated operations.
- LLM cost: tokens and estimated cost per route, model and day from an editable price table; hourly alerts when a route's cost per request or LLM traffic jumps, with the factor that rose.

### 6.7 Access control

- Ingest key (writes), read key (public dashboard reads while `PUBLIC_DASHBOARD` is on), project keys with `ingest` / `read` / `admin` scopes, dashboard users (admins see everything, members see their projects).

---

## 7. Non-Functional Requirements

- **SDK overhead:** measured on every SDK change (`bench/`); currently ~5–15 µs per request on ASGI/Fastify/Hono and ~19–33 µs on Flask and Express (p50).
- **Fail-open:** no SDK error may reach the host app's request path.
- **Correctness:** percentiles from mergeable sketches; a stray old event can't overwrite aggregate history.
- **Security:** keys and session tokens stored only as hashes; passwords argon2id; secrets only in env vars; request bodies capped at 16 MB.
- **Cost guard:** LLM calls are rate-limited per IP and capped per day (Ask), and report regeneration is cached for 10 minutes.
- **Reliability:** scheduled jobs tolerate a busy event loop (generous misfire grace); alert and SLO state live in Postgres.

---

## 8. Success Metrics

| Metric | Target |
|---|---|
| Live demo | Dashboard shows real EventFlow traffic on first visit, without signing in |
| SDK overhead | Under 50 µs per request p50 on every supported framework (benchmark in CI) |
| Ask Reqly accuracy | ≥ 90% of the eval set passes after any prompt/model change |
| Install | `pip install reqly` (Python 3.9–3.13) and `npm i reqly-node` (Node 20+) work; CI tests every version |
| Alert quality | Demo scenarios (bad deploy, bad pod, Monday incident) are each detected with the right lead |

---

## 9. Risks & Open Questions

- **LLM price table** — filled from published list prices as of 2025-10; must be checked before cost numbers are relied on (`LLM_PRICES_FILE` overrides it).
- **Public read key** — with `PUBLIC_DASHBOARD` on, anyone with the dashboard URL can read every project; it's meant for demos only.
- **Single instance** — the scheduler, rate limits and caches are per process; running two collectors duplicates jobs.
- **Provider dependence** — AI features need Groq; models get retired (Llama 3.3 already was), so the model is configurable and the reports fall back to plain text.
- **Open question:** should anomaly thresholds become per-service for low-traffic routes?

---

## 10. v2 Candidates

- **Docs site and a rewritten landing page** — the features outgrew the README.
- **Helm chart** — Kubernetes deploys without hand-written manifests.
- **Node SDK 0.2** — Koa/NestJS, a generic `http` wrapper, streamed byte counts; built when a real user asks (OTLP covers them today).
- **LLM cost from OTLP GenAI spans**, so OpenTelemetry apps get LLM cost and its alerts too.
- **OIDC sign-in** and multi-instance collectors.

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
