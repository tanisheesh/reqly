---
title: Node.js SDK
description: Instrument Express, Fastify, Hono, Koa, NestJS or any node:http handler with reqly-node.
---

# Node.js SDK

```bash
npm install reqly-node
```

Node 20+ and Bun. No runtime dependencies. ESM and CommonJS. [npm](https://www.npmjs.com/package/reqly-node) · [Changelog](https://github.com/tanisheesh/reqly/blob/main/sdk-node/CHANGELOG.md)

## Instrument your framework

=== "Express 4 / 5"

    ```js
    import express from "express";
    import { reqlyExpress } from "reqly-node";

    const app = express();
    const reqly = reqlyExpress({
      serviceName: "checkout-api",
      collectorUrl: "https://reqly.example.com",
      apiKey: process.env.REQLY_API_KEY,
    });
    app.use(reqly);               // before your routes
    // ...routes and routers...
    app.use(reqly.errorHandler);  // optional, after your routes: records the error's type
    ```

=== "Fastify 4 / 5"

    ```js
    import Fastify from "fastify";
    import { reqlyFastify } from "reqly-node";

    const app = Fastify();
    await app.register(reqlyFastify({ serviceName: "checkout-api" }));
    ```

=== "Hono (Node.js)"

    ```js
    import { Hono } from "hono";
    import { reqlyHono } from "reqly-node";

    const app = new Hono();
    app.use(reqlyHono({ serviceName: "checkout-api" }));
    ```

=== "Koa 2 / 3"

    ```js
    import Koa from "koa";
    import Router from "@koa/router";
    import { reqlyKoa } from "reqly-node";

    const app = new Koa();
    app.use(reqlyKoa({ serviceName: "checkout-api" }));  // first, before your routers
    app.use(router.routes()).use(router.allowedMethods());
    ```

    Routes are @koa/router templates, router prefixes included (`/api/users/:id`). A thrown error is recorded with its status (500, unless it carries one like `ctx.throw(404)`) and, for 5xx, its type; then it's rethrown for Koa to handle.

=== "NestJS"

    ```js
    import { NestFactory } from "@nestjs/core";
    import { reqlyNest } from "reqly-node";

    const app = await NestFactory.create(AppModule);   // Express or Fastify adapter
    reqlyNest(app, { serviceName: "checkout-api" });   // before app.listen()
    await app.listen(3000);
    ```

    Routes include the global prefix and controller path. Nest's catch-all 404 is recorded as `__unmatched__`. A global interceptor records the type of exceptions that become 5xx; `HttpException`s below 500 (`NotFoundException`, `BadRequestException`, …) aren't counted as errors. The returned object has `client` for `shutdown()`.

=== "node:http / other"

    For node:http, or a framework without an integration, wrap the `(req, res)` handler and give a `routeResolver` that returns the route template. Only the router knows it, so without one every request is recorded as `__unmatched__`, never as the raw path.

    ```js
    import http from "node:http";
    import { reqlyHttp } from "reqly-node";

    const handler = reqlyHttp(app, {
      serviceName: "checkout-api",
      routeResolver: (req) => req.matchedRoute,  // read when the response finishes
    });
    http.createServer(handler).listen(3000);
    ```

    An error the handler throws, or a promise it returns rejects with, is recorded with its type and rethrown.

=== "CommonJS"

    ```js
    const { reqlyExpress } = require("reqly-node");
    ```

Routes are recorded as templates (`/api/users/:id`, including router mount paths), never as raw paths. Requests that no route matched are recorded as `__unmatched__`.

## Who is calling

```js
reqlyExpress({ consumerHeader: "x-api-key", consumerSalt: process.env.REQLY_CONSUMER_SALT });

// or any logic:
reqlyExpress({ consumer: (info) => info.headers["x-tenant-id"] });
```

Ids are hashed with HMAC-SHA256 and your salt before they leave the process, so raw API keys never reach the collector. `hashConsumer: false` sends them as they are; use it only for non-secret ids such as tenant names. See [API consumers](../features/consumers.md).

## LLM cost per route

```js
import { recordLlmResponse, recordLlmUsage } from "reqly-node";

const completion = await openai.chat.completions.create({ model: "gpt-4o-mini", messages });
recordLlmResponse(completion);             // OpenAI- or Anthropic-style responses
recordLlmUsage("gpt-4o-mini", 1200, 240);  // or explicitly
```

Usage is tied to the request through `AsyncLocalStorage`, so it works after `await`s. See [LLM cost](../features/llm-cost.md).

## Configuration

Options passed to `reqlyExpress()` / `reqlyFastify()` / `reqlyHono()` (or to a shared `new ReqlyClient(options)`), else environment variables, else defaults:

| Option | Environment variable | Default |
|---|---|---|
| `serviceName` | `REQLY_SERVICE_NAME` | `npm_package_name`, else `unnamed-service` |
| `collectorUrl` | `REQLY_COLLECTOR_URL` | `http://localhost:8000` |
| `apiKey` | `REQLY_API_KEY` | none |
| `release` | `REQLY_RELEASE`, then `GITHUB_SHA`, `CI_COMMIT_SHA`, `RENDER_GIT_COMMIT`, `VERCEL_GIT_COMMIT_SHA`, … | auto-detected |
| `environment` | `REQLY_ENVIRONMENT` | none |
| `sampleRate` | `REQLY_SAMPLE_RATE` | `1` |
| `flushIntervalMs` | `REQLY_FLUSH_INTERVAL_MS` (or `REQLY_FLUSH_INTERVAL_SECONDS`) | `5000`; a full batch is sent at once |
| `maxBatchSize` | `REQLY_MAX_BATCH_SIZE` | `200` |
| `maxQueueSize` | `REQLY_MAX_QUEUE_SIZE` | `2000` |
| `ignoreRoutes` | `REQLY_IGNORE_ROUTES` (comma-separated) | `/health,/metrics` |
| `consumerHeader` | `REQLY_CONSUMER_HEADER` | none |
| `consumer` | — | none: `(info) => string | undefined` |
| `consumerSalt` | `REQLY_CONSUMER_SALT` | none: set it |
| `hashConsumer` | `REQLY_HASH_CONSUMER` | `true` |
| `pushOpenapi` | `REQLY_PUSH_OPENAPI` | `false`. See [OpenAPI drift](../features/openapi-drift.md) |

## Shutting down

Events still queued when the process exits on its own are sent automatically. On `SIGTERM`, or before calling `process.exit()`, flush them first:

```js
process.on("SIGTERM", async () => {
  await reqly.client.shutdown();
  process.exit(0);
});
```

## Guarantees

- **Small overhead:** about 6 µs per request on Hono, 10 µs on Fastify and 27 µs on Express ([benchmark](../reference/benchmarks.md)).
- **Fail-open:** nothing the SDK does can throw into your request path. An internal error disables instrumentation and logs once.
- **Non-blocking:** events are queued in memory and sent in batches by a timer that never keeps the process alive. The queue is bounded, and the oldest events are dropped first.
- **Retries:** 408, 429, 5xx and network errors are retried with backoff. Other 4xx responses drop the batch.

## Runtimes

- **Node.js 20+**: every integration.
- **Bun**: Hono on `Bun.serve` and `reqlyHttp` on Bun's node:http are tested in CI.
- **Cloudflare Workers, Deno Deploy and other edge runtimes**: not supported yet. A Worker can't keep a background flush timer between requests, so use [OpenTelemetry](opentelemetry.md) there.
