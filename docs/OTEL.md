# Any language via OpenTelemetry

Reqly's collector accepts OpenTelemetry traces over OTLP/HTTP, so an app in **any
language** that already uses (or can add) OpenTelemetry instrumentation shows up in the
Reqly dashboard — latency percentiles, error rates, status codes, deploy markers and the
weekly AI report — without a Reqly SDK.

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

Reqly only needs traces. If your setup also exports metrics or logs, either point those
elsewhere or turn them off (`OTEL_METRICS_EXPORTER=none`, `OTEL_LOGS_EXPORTER=none`).

| Resource attribute | Becomes in Reqly |
|---|---|
| `service.name` | service |
| `service.version` | release (powers deploy markers and deploy-aware insights) |
| `deployment.environment.name` (or legacy `deployment.environment`) | environment |
| `k8s.pod.name` / `host.name` | host |

## Per-language quick starts

**Node.js** (Express, Fastify, Koa, NestJS, Hono via `@opentelemetry/auto-instrumentations-node`) — verified:
```bash
npm install @opentelemetry/auto-instrumentations-node @opentelemetry/sdk-node
NODE_OPTIONS="--require @opentelemetry/auto-instrumentations-node/register" node app.js
```
A runnable example lives in [`examples/otel/express`](../examples/otel/express).

**Python** (any framework with an OpenTelemetry instrumentation) — verified with FastAPI:
```bash
pip install opentelemetry-distro opentelemetry-exporter-otlp
opentelemetry-bootstrap -a install
opentelemetry-instrument python app.py
```
For FastAPI and Flask, the native Reqly SDK (`pip install reqly`) is lighter and needs no
OpenTelemetry setup.

**Java** (Spring Boot, Quarkus, Micronaut, …):
```bash
java -javaagent:opentelemetry-javaagent.jar -jar app.jar
```

**.NET** — the OTLP exporter defaults to gRPC, so set
`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` explicitly.

**Go** — use the HTTP exporter (`go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp`)
and an HTTP instrumentation that records the route, e.g. `otelhttp` with
`otelhttp.WithRouteTag`, or your router's OpenTelemetry middleware.

Java, .NET and Go follow the same OTLP spec but haven't been run against Reqly yet —
reports welcome.

## What gets recorded

Reqly is API-level monitoring, not a tracing backend. From each export it keeps only
**SERVER spans that describe an HTTP request**; client calls, database spans, internal
spans and non-HTTP servers (gRPC, messaging) are skipped by design.

| Reqly field | From the span |
|---|---|
| method | `http.request.method` (legacy: `http.method`) |
| route | `http.route`, or `__unmatched__` if there isn't one |
| status code | `http.response.status_code` (legacy: `http.status_code`); a span with error status and no response counts as 500 |
| duration | span end − span start |
| error | status ≥ 500, or span status ERROR |
| error type | `error.type`, else the `exception.type` of the span's first exception event |
| body sizes | `http.request.body.size` / `http.response.body.size` |

**The route matters.** Reqly groups by route template (`/users/:id`), never by raw URL. If
your instrumentation doesn't set `http.route`, all requests land in `__unmatched__` —
check your framework's instrumentation docs for how to enable route reporting.

Each span gets a deterministic event id derived from its trace and span id, so when an
exporter retries a failed export, the duplicate spans are dropped instead of counted twice.

## Limits and responses

- Content types: `application/x-protobuf`, `application/json`; `Content-Encoding: gzip` supported.
- Request body ≤ 8 MB (≤ 32 MB decompressed); ≤ 10,000 HTTP server spans per request.
- Same ingest key and rate limit as `/v1/ingest`.
- Spans that look like HTTP server spans but can't be used (no status code, negative
  duration, …) are reported back to the exporter as `partial_success.rejected_spans` with a
  short reason; the rest of the batch is stored.
