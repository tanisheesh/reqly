---
title: Introduction
description: Reqly is self-hosted API monitoring for Python, Node.js and any OpenTelemetry stack.
---

# Reqly

**Self-hosted API monitoring that tells you what broke, when, and which deploy did it** — for Python, Node.js and any OpenTelemetry stack.

Reqly watches every request your APIs serve and explains problems instead of just charting them. When a route starts failing, it tells you which release was running, which host or client the errors came from, and whether you're burning your error budget. You can also ask it why in plain English. Everything runs on your own Postgres: one `docker compose up` or `helm install`, a one-line SDK, or plain OpenTelemetry.

<div class="grid cards" markdown>

-   :material-rocket-launch: **Quickstart**

    ---

    Run the stack and see your first metrics in five minutes.

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

-   :material-language-python: **Python**

    ---

    FastAPI, Flask, Django, Starlette, Litestar and any WSGI/ASGI app.

    [:octicons-arrow-right-24: Python SDK](instrument/python.md)

-   :material-nodejs: **Node.js**

    ---

    Express, Fastify, Hono, Koa and NestJS, with no runtime dependencies.

    [:octicons-arrow-right-24: Node SDK](instrument/node.md)

-   :material-transit-connection-variant: **Any language**

    ---

    Point an OpenTelemetry exporter at Reqly. Go, Java, .NET and more.

    [:octicons-arrow-right-24: OpenTelemetry](instrument/opentelemetry.md)

</div>

## What you get

| | |
|---|---|
| **Metrics that are right** | p50/p95/p99 per route and per service from mergeable percentile sketches, error rates, status codes, deploy markers and per-release health. [Metrics & releases](features/metrics.md) |
| **It explains what broke** | Hourly alerts against a weekday × hour baseline, with the release that was running, root-cause leads and the clients that were hit. A weekly AI report narrates the findings. [Alerts](features/alerts.md) |
| **Ask Reqly** | *"Why did /orders start failing?"* answered from your own data through read-only query tools, with every query shown and every number checked. [Ask Reqly](features/ask-reqly.md) |
| **API-level depth** | [SLOs with burn-rate alerts](features/slos.md), [OpenAPI drift](features/openapi-drift.md), [API consumers](features/consumers.md) and [LLM cost per route](features/llm-cost.md). |
| **Small and safe** | The SDKs add ~6–33 µs per request ([measured](reference/benchmarks.md)) and never raise into your app. No request bodies, query strings or headers are captured. |
| **For teams** | [Projects, scoped API keys and sign-in](self-hosting/access.md) when several teams share one collector. |

## How it fits together

```
[Python app + reqly]        ──►  POST /v1/ingest
[Node app + reqly-node]     ──►  POST /v1/ingest
[Any app + OpenTelemetry]   ──►  POST /otlp/v1/traces
                                        │
                              [Collector — FastAPI]
                                        │
                              [TimescaleDB + Toolkit]
                                        │
          ┌─────────────────────────────┼──────────────────────────┐
     [Dashboard]                  [Scheduler]                 [Ask Reqly]
   charts, panels,          hourly alerts, SLO burn,      read-only tools →
   settings, sign-in        weekly report → Slack …       Groq → verified answer
```

- **SDKs** record each request (method, route template, status, duration, error type, release, sizes) into a bounded queue and ship batches in the background.
- **The collector** validates events, stores them in a TimescaleDB hypertable, and keeps continuous aggregates current. It also runs the alert and SLO checks and the weekly report.
- **The dashboard** is a static React app that reads from the collector.

## Try it

- **Live dashboard:** [reqly-eventflow-dashboard.onrender.com](https://reqly-eventflow-dashboard.onrender.com). It shows real traffic from [EventFlow](https://eventflow-g2h5.onrender.com), a Flask app instrumented with Reqly.
- **Source:** [github.com/tanisheesh/reqly](https://github.com/tanisheesh/reqly). The server is AGPL-3.0, the SDKs MIT
- **Design docs:** [PRD](https://github.com/tanisheesh/reqly/blob/main/docs/PRD.md) · [Architecture](https://github.com/tanisheesh/reqly/blob/main/docs/ARCHITECTURE.md) · [Decisions](https://github.com/tanisheesh/reqly/blob/main/docs/DECISIONS.md)
