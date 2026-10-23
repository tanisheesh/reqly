<p align="center">
  <svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24"
       fill="none" stroke="#06b6d4" stroke-width="1.5"
       stroke-linecap="round" stroke-linejoin="round">
    <path d="M2 12h3l3-8 4 16 3-10 2 2h5"/>
  </svg>
</p>

<h1 align="center">Reqly</h1>

<p align="center">
  <strong>Self-hosted API monitoring that tells you what broke, when, and which deploy did it — for Python, Node.js and any OpenTelemetry stack.</strong>
</p>

<p align="center">
  <a href="https://reqly-eventflow-dashboard.onrender.com">
    <img src="https://img.shields.io/badge/live_demo-06b6d4-06b6d4?style=flat-square" alt="Live Demo">
  </a>
  <a href="https://reqly.tanisheesh.in"><img src="https://img.shields.io/badge/website-reqly.tanisheesh.in-06b6d4?style=flat-square" alt="Website"></a>
  <a href="https://reqly.tanisheesh.in/docs/"><img src="https://img.shields.io/badge/docs-reqly.tanisheesh.in%2Fdocs-06b6d4?style=flat-square" alt="Docs"></a>
  <a href="https://pypi.org/project/reqly/"><img src="https://img.shields.io/pypi/v/reqly?color=06b6d4&label=pypi&style=flat-square" alt="PyPI"></a>
  <a href="https://www.npmjs.com/package/reqly-node"><img src="https://img.shields.io/npm/v/reqly-node?color=06b6d4&label=npm&style=flat-square" alt="npm"></a>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-61DAFB?style=flat-square&logo=react&logoColor=black" alt="React">
  <img src="https://img.shields.io/badge/TimescaleDB-FDB515?style=flat-square&logo=postgresql&logoColor=black" alt="TimescaleDB">
  <img src="https://img.shields.io/badge/OpenTelemetry-000000?style=flat-square&logo=opentelemetry&logoColor=white" alt="OpenTelemetry">
  <img src="https://img.shields.io/badge/LLM-any_OpenAI--compatible-412991?style=flat-square" alt="Any OpenAI-compatible LLM">
  <img src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
  <a href="#license"><img src="https://img.shields.io/badge/license-AGPL--3.0_%C2%B7_SDKs_MIT-06b6d4?style=flat-square" alt="License: AGPL-3.0, SDKs MIT"></a>
</p>

## What is Reqly?

Reqly watches every request your APIs serve and explains problems instead of just charting them: when a route starts failing it says which release was running, which host or client the errors came from, and whether you are burning your error budget — and you can ask it why in plain English. It is a lightweight, self-hosted alternative to Datadog-style APMs for teams that want their data on their own Postgres: one `docker compose up` (or `helm install`, or a single all-in-one container), a one-line SDK, or plain OpenTelemetry.

```python
import reqly
reqly.instrument(app, service_name="checkout-api")   # FastAPI, Flask, Django, Starlette, Litestar
```

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/tanisheesh/reqly) · or [Coolify, Railway, Docker, Helm](https://reqly.tanisheesh.in/docs/self-hosting/one-click/)

> **Live demo →** [reqly-eventflow-dashboard.onrender.com](https://reqly-eventflow-dashboard.onrender.com) — metrics from the
> [EventFlow](https://eventflow-g2h5.onrender.com) demo app (log in there as `admin@eventhub.com` / `Admin@123` to make some traffic).


## What you get

- **Metrics that are right** — p50/p95/p99 per route and per service from mergeable percentile sketches, error rates, status codes, deploy markers and per-release health
- **It explains what broke** — hourly alerts against a weekday × hour baseline, for errors, latency and sudden 401/403/422/429 waves, with the release that was running, root-cause leads (*"92% of errors came from pod-7"*), the clients that were hit and traces to open; to Slack, Discord, Teams, PagerDuty, Opsgenie, email or webhooks, acknowledged or muted from the dashboard; a weekly AI report narrates the findings
- **Ask Reqly** — *"why did /orders start failing?"* answered from the collector's own data through read-only query tools, with every query shown and any number not found in the results flagged
- **API-level depth** — SLOs with burn-rate alerts, OpenAPI drift (undocumented, unused and deprecated-but-used endpoints), API consumers, and LLM token cost per route and model (counted straight from your OpenAI or Anthropic client) with alerts when it spikes
- **Any stack, small footprint** — Python SDK, Node.js SDK (Express, Fastify, Hono, Koa, NestJS; Cloudflare Workers, Vercel and Next.js with `reqly-node/edge`), OTLP from any language, or your reverse proxy's access logs (Caddy, Traefik, Nginx, Envoy); deploy markers from CI with the `reqly` CLI or the GitHub Action; the SDKs add ~6–33 µs per request ([benchmark](bench/README.md)) and never crash your app; projects, per-team API keys and sign-in for shared collectors; safe by default, with Prometheus metrics for the collector itself


## Stack

| Layer | Tech |
|---|---|
| SDKs | Python 3.9+ (pure ASGI/WSGI middleware, httpx) · Node.js 20+ (TypeScript, zero dependencies) · OTLP/HTTP |
| Collector | FastAPI · asyncpg · APScheduler · Pydantic v2 · argon2 |
| Database | TimescaleDB (pg16, Toolkit) · hypertables · continuous aggregates · UddSketch |
| Dashboard | React 19 · Vite · TypeScript · Tailwind CSS 4 · Recharts · TanStack Query |
| AI | Any OpenAI-compatible API (Groq by default; OpenAI, OpenRouter, Ollama) · gpt-oss-120b (tool calling) · statistics-first anomaly detection |
| Infra | Docker Compose · Helm chart · all-in-one image · Render / Coolify / Railway templates · EC2 (TimescaleDB) · Render · GHCR images · PyPI / npm trusted publishing · CI with ruff, mypy, vitest and an end-to-end test |


## Engineering Decisions

**Why statistics before the LLM?**
Anomalies come from a Poisson / z-score test against each route's weekday-hour baseline; the LLM only narrates confirmed findings. Detection stays deterministic and auditable, and nothing is sent to a model when nothing is wrong.

**Why mergeable percentile sketches instead of storing p95s?**
Percentiles can't be averaged. A UddSketch per route-minute merges exactly across routes and time, so a service's 7-day p95 is a real p95 (within ~0.2%), not the max of per-minute p95s that overstated latency.

**Why fixed query tools for Ask Reqly, not text-to-SQL?**
The read key ships in the dashboard, so generated SQL would make it a SQL console. Nine validated, read-only tools return rounded aggregates, and every number in the answer is checked against what they returned.

**Why measure SDK overhead?**
"Zero overhead" was a claim; the benchmark made it a number — and found a Node bug that sent one collector request per app request under steady traffic, plus Python work that could move off the request path (overhead halved).

**Why claim scheduled runs in the database?**
Every collector runs the scheduler. Each run first inserts its (job, slot) row, and only the one whose insert lands goes ahead, so a rolling deploy or a stray second replica can't send alerts twice or pay for the weekly AI report twice, whatever the timing.

**What would you do differently in v2?**
Design rate limits and the Ask Reqly budget as shared state from the start: they're per process today, which is fine for self-hosting but caps horizontal scaling.


## Docs

Using Reqly? Start with the **[documentation site](https://reqly.tanisheesh.in/docs/)**: quickstart, SDK guides, features, self-hosting and the HTTP API. The documents below are about how Reqly is built.

| Document | Description |
|---|---|
| [PRD](docs/PRD.md) | Product requirements — goals, user stories, non-goals |
| [Architecture](docs/ARCHITECTURE.md) | System design, data flow, component breakdown |
| [Decisions](docs/DECISIONS.md) | Every major technical decision and why |
| [Setup](docs/SETUP.md) | Run the repository locally, env vars |
| [Deploy](docs/DEPLOY.md) | Production deployment, releases, rollback |
| [Benchmarks](bench/README.md) | SDK overhead per framework and how it's measured |
| [Contributing](CONTRIBUTING.md) | How to propose changes |
| [Changelog](CHANGELOG.md) | What changed in each release |
| [Security](SECURITY.md) | How to report a vulnerability |


## License

| Part | License |
|---|---|
| Collector, dashboard, Helm chart and everything else in this repository | [AGPL-3.0](LICENSE) |
| Python SDK ([`sdk/`](sdk), `pip install reqly`) | [MIT](sdk/LICENSE) |
| Node.js SDK ([`sdk-node/`](sdk-node), `npm install reqly-node`) | [MIT](sdk-node/LICENSE) |

The SDKs run inside your app, so they're MIT: use them in any project, open or closed. The
server is AGPL-3.0: run, modify and self-host it freely; if you offer a modified version as a
service, share its source.

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hi@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hi%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
