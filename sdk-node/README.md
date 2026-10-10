# reqly-node

Node.js SDK for [Reqly](https://github.com/tanisheesh/reqly), a self-hosted API
observability tool: per-route p50/p95/p99 latency, error rates, deploy-aware alerts,
SLOs, API consumers and LLM cost, sent to a Reqly collector you run.

Express, Fastify, Hono, Koa and NestJS in one line. No runtime dependencies; Node 20+.

```bash
npm install reqly-node
```

## Usage

**Express** (4 and 5)

```js
import express from "express";
import { reqlyExpress } from "reqly-node";

const app = express();
const reqly = reqlyExpress({
  serviceName: "checkout-api",
  collectorUrl: "https://reqly.example.com",
  apiKey: process.env.REQLY_API_KEY,
});
app.use(reqly);            // before your routes
// ...routes and routers...
app.use(reqly.errorHandler); // optional, after your routes: records the error's type
```

**Fastify** (4 and 5)

```js
import Fastify from "fastify";
import { reqlyFastify } from "reqly-node";

const app = Fastify();
await app.register(reqlyFastify({ serviceName: "checkout-api" }));
```

**Hono** (Node.js)

```js
import { Hono } from "hono";
import { reqlyHono } from "reqly-node";

const app = new Hono();
app.use(reqlyHono({ serviceName: "checkout-api" }));
```

**Koa** (2 and 3, with @koa/router)

```js
import Koa from "koa";
import { reqlyKoa } from "reqly-node";

const app = new Koa();
app.use(reqlyKoa({ serviceName: "checkout-api" })); // first, before your routers
app.use(router.routes());
```

**NestJS** (Express or Fastify adapter)

```js
import { NestFactory } from "@nestjs/core";
import { reqlyNest } from "reqly-node";

const app = await NestFactory.create(AppModule);
reqlyNest(app, { serviceName: "checkout-api" }); // before app.listen()
await app.listen(3000);
```

Nest routes include the global prefix and controller path; exceptions that become 5xx are
recorded with their type, `HttpException`s below 500 (`NotFoundException`, …) are not errors.

Routes are recorded as templates (`/api/users/:id`, including router mount paths),
never as raw paths; requests no route matched are recorded as `__unmatched__`. CommonJS
works too: `const { reqlyExpress } = require("reqly-node")`.

### Who is calling

```js
reqlyExpress({ consumerHeader: "x-api-key", consumerSalt: process.env.REQLY_CONSUMER_SALT });
// or any logic: consumer: (info) => info.headers["x-tenant-id"]
```

Ids are HMAC-SHA256-hashed with your salt before they leave the process; raw API keys never
reach the collector. `hashConsumer: false` sends them as they are (only for non-secret ids
such as tenant names).

### LLM cost per route

Record token usage where you call a model; the collector prices it per route:

```js
import { recordLlmResponse, recordLlmUsage } from "reqly-node";

const completion = await openai.chat.completions.create({ model: "gpt-4o-mini", messages });
recordLlmResponse(completion);      // OpenAI- or Anthropic-style responses
recordLlmUsage("gpt-4o-mini", 1200, 240); // or explicitly
```

Usage is tied to the request through `AsyncLocalStorage`, so it works after `await`s.

## Configuration

Options passed to `reqlyExpress()` / `reqlyFastify()` / `reqlyHono()` (or a shared
`new ReqlyClient(options)`), else environment variables, else defaults:

| option | environment variable | default |
|---|---|---|
| `serviceName` | `REQLY_SERVICE_NAME` | `npm_package_name`, else `unnamed-service` |
| `collectorUrl` | `REQLY_COLLECTOR_URL` | `http://localhost:8000` |
| `apiKey` | `REQLY_API_KEY` | none |
| `release` | `REQLY_RELEASE`, then `GITHUB_SHA`, `CI_COMMIT_SHA`, `RENDER_GIT_COMMIT`, `VERCEL_GIT_COMMIT_SHA`, … | auto-detected |
| `environment` | `REQLY_ENVIRONMENT` | none |
| `sampleRate` | `REQLY_SAMPLE_RATE` | `1` |
| `flushIntervalMs` | `REQLY_FLUSH_INTERVAL_MS` | `5000` |
| `maxBatchSize` | `REQLY_MAX_BATCH_SIZE` | `200` |
| `maxQueueSize` | `REQLY_MAX_QUEUE_SIZE` | `2000` |
| `ignoreRoutes` | `REQLY_IGNORE_ROUTES` (comma-separated) | `/health,/metrics` |
| `consumerHeader` | `REQLY_CONSUMER_HEADER` | none |
| `consumer` | — | none: `(info) => string \| undefined` |
| `consumerSalt` | `REQLY_CONSUMER_SALT` | none (set it) |
| `hashConsumer` | `REQLY_HASH_CONSUMER` | `true` |

Events still queued when the process exits on its own are sent automatically; on `SIGTERM`
or `process.exit()`, `await reqly.client.shutdown()` first.

## Guarantees

- **Small overhead:** about 6 µs per request on Hono, 10 µs on Fastify and 27 µs on Express,
  with consumer tracking on and the shipper running
  ([benchmark](https://github.com/tanisheesh/reqly/blob/main/bench/README.md)).
- **Fail-open:** nothing the SDK does can throw into your request path; an internal error
  disables instrumentation and logs once.
- **Non-blocking:** events are queued in memory and sent in batches by a timer that never
  keeps the process alive. The queue is bounded (oldest events dropped first).
- **Retries:** 408/429/5xx and network errors are retried with backoff; other 4xx drop the
  batch.

Prefer OpenTelemetry? Reqly also ingests OTLP traces — see the
[OpenTelemetry guide](https://reqly.tanisheesh.in/docs/instrument/opentelemetry/).

Full documentation: **[reqly.tanisheesh.in/docs](https://reqly.tanisheesh.in/docs/instrument/node/)**.

## License

GPL-3.0
