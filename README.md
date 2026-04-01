<p align="center">
  <svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24"
       fill="none" stroke="#06b6d4" stroke-width="1.5"
       stroke-linecap="round" stroke-linejoin="round">
    <path d="M2 12h3l3-8 4 16 3-10 2 2h5"/>
  </svg>
</p>

<h1 align="center">Reqly</h1>

<p align="center">
  <strong>Self-hostable API observability SDK for FastAPI and Flask — 2 lines of code, zero config, weekly AI anomaly reports</strong>
</p>

<p align="center">
  <a href="https://reqly.tanisheesh.in">
    <img src="https://img.shields.io/badge/website-reqly.tanisheesh.in-06b6d4?style=flat-square" alt="Website">
  </a>
  <a href="https://pypi.org/project/reqly/">
    <img src="https://img.shields.io/pypi/v/reqly?color=06b6d4&label=reqly&style=flat-square" alt="PyPI">
  </a>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-61DAFB?style=flat-square&logo=react&logoColor=black" alt="React">
  <img src="https://img.shields.io/badge/TimescaleDB-FDB515?style=flat-square&logo=postgresql&logoColor=black" alt="TimescaleDB">
  <img src="https://img.shields.io/badge/Groq-F55036?style=flat-square" alt="Groq">
  <img src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/license-GPL--3.0-06b6d4?style=flat-square" alt="License">
</p>

---

## What is Reqly?

Reqly is a self-hostable APM tool — a lightweight, honestly-scoped alternative to Datadog/New Relic for Python microservices. Add one call to your app; Reqly captures every request and surfaces real-time metrics plus a weekly AI-generated anomaly report powered by Groq (Llama 3.3-70b). The entire stack — collector, TimescaleDB, dashboard — runs with `docker compose up`.

```python
import reqly
from fastapi import FastAPI

app = FastAPI()
reqly.instrument(app, service_name="checkout-api")
# Every route is now tracked — latency · error rate · status codes
```

> **Live demo →** [reqly-eventflow-dashboard.onrender.com](https://reqly-eventflow-dashboard.onrender.com)
> Demo instrumented app at [eventflow-g2h5.onrender.com](https://eventflow-g2h5.onrender.com) — login as `admin@eventhub.com` / `Admin@123`, then click **Metrics**.

---

## What you get

- **Latency percentiles** — p50 / p95 / p99 per route, across 1h / 6h / 24h / 7d windows with per-route drill-down
- **Error rates & status distribution** — 2xx / 3xx / 4xx / 5xx breakdown over time, top routes ranked by volume and error rate
- **AI anomaly reports** — weekly z-score detection against a day-of-week × hour-of-day seasonal baseline, with Groq writing the narrative. Degrades to plain-text stats if no API key is set.
- **Zero-overhead SDK** — fail-open (never crashes your app), non-blocking background thread, bounded 2 000-event queue, bounded cardinality (route templates, never raw paths)

---

## Stack

| Layer | Tech |
|---|---|
| SDK | Python 3.9+ · threading · httpx · pure ASGI/WSGI middleware |
| Collector | FastAPI 0.110 · asyncpg · APScheduler · slowapi · Pydantic v2 |
| Database | TimescaleDB (pg16) · hypertables · continuous aggregates |
| Dashboard | React 19 · Vite 8 · TypeScript · Tailwind CSS 4 · Recharts · TanStack Query |
| AI | Groq API · Llama 3.3-70b-versatile |
| Infra | Docker Compose (local) · EC2 t3.small + AWS Lambda (prod) |

---

## Engineering Decisions

**Why z-score before the LLM, not LLM-first?**
Anomaly detection runs as a pure statistics pass — z-score over a day-of-week × hour-of-day seasonal baseline — before the LLM ever sees data. The model's job is to write a readable narrative, not decide what's anomalous. This keeps detection deterministic, auditable, and cheap; the LLM only runs when there are confirmed deviations to explain.

**Why fail-open and how is it implemented?**
Reqly runs inside your app's process. Every instrumentation path is wrapped so any internal exception is caught, logged once, and the SDK self-disables. The background flush thread is a daemon thread; all outbound HTTP calls carry strict connect + read timeouts — a slow collector never blocks a request thread.

**Why TimescaleDB over regular Postgres?**
Reqly stores one row per request — millions of rows fast. Every dashboard query is a time-range aggregation. TimescaleDB's hypertables partition the table into day-sized chunks automatically, so a 6-hour query touches only relevant chunks. Continuous aggregates pre-compute per-minute rollups on insert, not on every page load.

**Why route templates instead of raw paths?**
Raw paths like `/users/1` and `/users/99999` become distinct metric labels — cardinality explodes. Reqly captures the framework's matched route template so all user lookups collapse to `/users/{id}`. Cardinality stays O(number of routes), not O(unique URLs ever requested).

---

## Docs

| Document | Description |
|---|---|
| [PRD](docs/PRD.md) | Product requirements — goals, user stories, non-goals |
| [Architecture](docs/ARCHITECTURE.md) | System design, data flow, component breakdown |
| [Decisions](docs/DECISIONS.md) | Every major technical decision and why |
| [Setup](docs/SETUP.md) | Local dev setup, env vars, deployment |

---

## Author

**Tanish Poddar** — [tanisheesh.in](https://tanisheesh.in) · [LinkedIn](https://linkedin.com/in/tanisheesh) · [GitHub](https://github.com/tanisheesh)
