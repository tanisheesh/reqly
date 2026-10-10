---
title: Quickstart
description: Run Reqly locally with Docker Compose and instrument your first app.
---

# Quickstart

Run the full stack locally, then point an app at it. You need Docker with Compose, plus Python 3.9+ or Node 20+ for the app you instrument.

## 1. Start the stack

```bash
git clone https://github.com/tanisheesh/reqly
cd reqly
docker compose up -d
```

No `.env` is needed; the defaults work locally. The first build takes a couple of minutes. Compose starts four containers:

| Container | Address | What it does |
|---|---|---|
| `timescaledb` | `localhost:5432` | TimescaleDB with the Toolkit. The collector applies the schema on start-up |
| `collector` | [localhost:8000](http://localhost:8000/docs) | Ingest, OTLP, metrics API, alerts, AI. `/docs` is the interactive API reference |
| `dashboard` | [localhost:5173](http://localhost:5173) | The web UI. Open this |
| `load-generator` | — | Backfills 8 weeks of synthetic history, then sends ~2 requests/second, including a few deliberate incidents |

Open [localhost:5173](http://localhost:5173), pick a service, and the charts fill in within a minute. The backfilled history takes a little longer to show up in the 7-day view and the weekly report.

## 2. Instrument your app

The local stack accepts the ingest key `demo-key`.

=== "Python"

    ```bash
    pip install reqly
    ```

    ```python
    import reqly
    from fastapi import FastAPI   # or Flask, Starlette, Litestar

    app = FastAPI()
    reqly.instrument(
        app,
        service_name="my-api",
        collector_url="http://localhost:8000",
        api_key="demo-key",
    )
    ```

    Django and other frameworks: see [Python](instrument/python.md).

=== "Node.js"

    ```bash
    npm install reqly-node
    ```

    ```js
    import express from "express";
    import { reqlyExpress } from "reqly-node";

    const app = express();
    app.use(reqlyExpress({
      serviceName: "my-api",
      collectorUrl: "http://localhost:8000",
      apiKey: "demo-key",
    }));
    ```

    Fastify, Hono, Koa and NestJS: see [Node.js](instrument/node.md).

=== "OpenTelemetry"

    ```bash
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000/otlp
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
    OTEL_EXPORTER_OTLP_HEADERS=x-reqly-key=demo-key
    OTEL_SERVICE_NAME=my-api
    ```

    Per-language setup: see [OpenTelemetry](instrument/opentelemetry.md).

Send a few requests to your app. The service appears in the dashboard's service list. Data arrives every 5 seconds and shows in the charts within about a minute.

## 3. Optional: AI and alerts

Copy `.env.example` to `.env` and set what you need, then `docker compose up -d` again:

```bash
GROQ_API_KEY=gsk_...                 # AI weekly report and Ask Reqly (free key: console.groq.com/keys)
ALERT_SLACK_WEBHOOK_URL=https://...  # or ALERT_DISCORD_WEBHOOK_URL / ALERT_WEBHOOK_URL
```

Without a Groq key, the weekly report is a plain statistical summary and Ask Reqly is off. Every setting is listed in [Configuration](self-hosting/configuration.md).

## Stop or reset

```bash
docker compose down      # stop, keep data
docker compose down -v   # stop and wipe the database
```

## Next steps

- [Alerts & weekly report](features/alerts.md): what triggers an alert and what it contains
- [SLOs](features/slos.md): define an availability or latency objective
- [Deploy to production](self-hosting/deploy.md): run Reqly on your own servers
