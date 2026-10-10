---
title: OpenTelemetry
description: Send traces from any language to Reqly over OTLP/HTTP — no Reqly SDK needed.
---

# OpenTelemetry

Reqly's collector accepts OpenTelemetry traces over OTLP/HTTP. Any app that uses, or can add, OpenTelemetry instrumentation gets the same per-route metrics, alerts and reports as one using a Reqly SDK.

```
Your app + OpenTelemetry SDK  ──OTLP/HTTP──►  POST /otlp/v1/traces  ──►  Reqly collector
```

## Configure the exporter

Every official OpenTelemetry SDK reads the same environment variables:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://<your-collector>/otlp   # exporters append /v1/traces
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf                   # or http/json; gRPC is not supported
OTEL_EXPORTER_OTLP_HEADERS=x-reqly-key=<your ingest key>
OTEL_SERVICE_NAME=checkout-api
OTEL_RESOURCE_ATTRIBUTES=service.version=<git sha>,deployment.environment.name=prod
```

Reqly only needs traces. If your setup also exports metrics or logs, point those elsewhere or turn them off (`OTEL_METRICS_EXPORTER=none`, `OTEL_LOGS_EXPORTER=none`).

| Resource attribute | Becomes in Reqly |
|---|---|
| `service.name` | service |
| `service.version` | release (deploy markers and deploy-aware alerts) |
| `deployment.environment.name` (or legacy `deployment.environment`) | environment |
| `k8s.pod.name` / `host.name` | host |

## Per-language setup

=== "Node.js"

    Express, Fastify, Koa, NestJS and Hono, through the auto-instrumentations. Verified against Reqly.

    ```bash
    npm install @opentelemetry/auto-instrumentations-node @opentelemetry/sdk-node
    NODE_OPTIONS="--require @opentelemetry/auto-instrumentations-node/register" node app.js
    ```

    A runnable example is in [`examples/otel/express`](https://github.com/tanisheesh/reqly/tree/main/examples/otel/express). For Express, Fastify and Hono there is also the native [Node SDK](node.md): one middleware line, plus consumer and LLM-cost tracking that OTLP doesn't carry.

=== "Python"

    Any framework with an OpenTelemetry instrumentation. Verified with FastAPI.

    ```bash
    pip install opentelemetry-distro opentelemetry-exporter-otlp
    opentelemetry-bootstrap -a install
    opentelemetry-instrument python app.py
    ```

    The native [Python SDK](python.md) is lighter and needs no OpenTelemetry setup.

=== "Java"

    Spring Boot, Quarkus, Micronaut and others, with the Java agent:

    ```bash
    java -javaagent:opentelemetry-javaagent.jar -jar app.jar
    ```

=== ".NET"

    The OTLP exporter defaults to gRPC, so set the protocol explicitly:

    ```bash
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
    ```

=== "Go"

    Use the HTTP exporter (`go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp`) and an HTTP instrumentation that records the route, for example `otelhttp` with `otelhttp.WithRouteTag`, or your router's OpenTelemetry middleware.

Java, .NET and Go follow the same OTLP spec but haven't been run against Reqly yet. Reports are welcome.

## What gets recorded

Reqly is API-level monitoring, not a tracing backend. From each export it keeps only **server spans that describe an HTTP request**. Client calls, database spans, internal spans and non-HTTP servers (gRPC, messaging) are skipped by design.

| Reqly field | From the span |
|---|---|
| method | `http.request.method` (legacy: `http.method`) |
| route | `http.route`, or `__unmatched__` if there isn't one |
| status code | `http.response.status_code` (legacy: `http.status_code`). A span with error status and no response counts as 500 |
| duration | span end − span start |
| error | status ≥ 500, or span status ERROR |
| error type | `error.type`, else the `exception.type` of the span's first exception event |
| body sizes | `http.request.body.size` / `http.response.body.size` |

!!! warning "The route matters"
    Reqly groups by route template (`/users/:id`), never by raw URL. If your instrumentation doesn't set `http.route`, every request lands in `__unmatched__`. Check your framework's instrumentation docs for how to turn on route reporting.

Each span gets a deterministic event id derived from its trace and span id. When an exporter retries a failed export, the duplicates are dropped instead of counted twice.

## Limits and responses

- Content types: `application/x-protobuf` and `application/json`; `Content-Encoding: gzip` is supported.
- Request body ≤ 8 MB (≤ 32 MB decompressed); ≤ 10,000 HTTP server spans per request.
- Same ingest key and rate limit as `/v1/ingest`. A [project key](../self-hosting/access.md) with the `ingest` scope works too.
- Spans older than 13 days or more than 15 minutes in the future are refused.
- Spans that look like HTTP server spans but can't be used (no status code, negative duration, …) are reported back to the exporter as `partial_success.rejected_spans` with a short reason. The rest of the batch is stored.
