<p align="center">
  <a href="https://reqly.tanisheesh.in"><img src="https://reqly.tanisheesh.in/docs/assets/logo.svg" width="64" height="64" alt="Reqly"></a>
</p>

<h1 align="center">reqly-node</h1>

<p align="center">
  <strong>API monitoring for Node.js that tells you what broke, when, and which deploy did it.</strong><br>
  Express · Fastify · Hono · Koa · NestJS · plain node:http, in one line. Zero dependencies.
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/reqly-node"><img src="https://img.shields.io/npm/v/reqly-node?color=06b6d4&style=flat-square&label=npm" alt="npm version"></a>
  <a href="https://www.npmjs.com/package/reqly-node"><img src="https://img.shields.io/npm/dm/reqly-node?color=06b6d4&style=flat-square&label=downloads" alt="Downloads"></a>
  <img src="https://img.shields.io/badge/node-%E2%89%A520-06b6d4?style=flat-square" alt="Node 20+">
  <img src="https://img.shields.io/badge/dependencies-0-06b6d4?style=flat-square" alt="Zero dependencies">
  <a href="https://github.com/tanisheesh/reqly/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-06b6d4?style=flat-square" alt="License"></a>
</p>

<p align="center">
  <a href="https://reqly.tanisheesh.in/docs/instrument/node/"><strong>Documentation</strong></a> ·
  <a href="https://reqly.tanisheesh.in/docs/quickstart/">Quickstart</a> ·
  <a href="https://reqly-eventflow-dashboard.onrender.com">Live demo</a> ·
  <a href="https://github.com/tanisheesh/reqly">GitHub</a>
</p>

---

## Install

```bash
npm install reqly-node
```

```js
import express from "express";
import { reqlyExpress } from "reqly-node";

const app = express();
app.use(reqlyExpress({
  serviceName: "checkout-api",
  collectorUrl: "https://reqly.example.com",
  apiKey: process.env.REQLY_API_KEY,
}));
```

That's it: every route now reports its latency, errors, status codes and release to your
own [Reqly collector](https://github.com/tanisheesh/reqly). No agent, no vendor account.
ESM and CommonJS (`require("reqly-node")`) both work.

## What you get

| | |
|---|---|
| 📈 **Real percentiles** | p50 / p95 / p99 per route and per service, merged exactly across routes and time |
| 🚀 **Deploy-aware** | The release is picked up from your CI or host (`GITHUB_SHA`, `RENDER_GIT_COMMIT`, …); every alert says which release was running |
| 🔔 **Alerts that explain** | Hourly checks against each route's weekday × hour baseline, naming the host, error type and clients behind a spike; to Slack, Discord or a webhook |
| 💬 **Ask Reqly** | *"Why did /orders start failing?"* answered from your own data, every number checked |
| 🎯 **SLOs** | Availability and latency objectives with error budgets and burn-rate alerts |
| 🧾 **OpenAPI drift** | Undocumented, unused and deprecated-but-called endpoints (`pushOpenapi`) |
| 👥 **API consumers** | Who calls your API and who an incident hit, with ids hashed in the SDK |
| 💸 **LLM cost per route** | Tokens and cost per route and model, and an alert when it spikes |

## Your framework

| Framework | What to write |
|---|---|
| Express 4 / 5 | `app.use(reqlyExpress(options))` before your routes; `app.use(reqly.errorHandler)` after them records error types |
| Fastify 4 / 5 | `await app.register(reqlyFastify(options))` |
| Hono | `app.use(reqlyHono(options))` (Node.js and Bun) |
| Koa 2 / 3 | `app.use(reqlyKoa(options))` first, before your routers |
| NestJS | `reqlyNest(app, options)` before `app.listen()` (Express or Fastify adapter) |
| node:http, anything else | `http.createServer(reqlyHttp(handler, { routeResolver }))` |

```js
// NestJS
const app = await NestFactory.create(AppModule);
reqlyNest(app, { serviceName: "checkout-api" });
await app.listen(3000);

// plain node:http: tell Reqly where the route template is
http.createServer(reqlyHttp(handler, {
  serviceName: "checkout-api",
  routeResolver: (req) => req.matchedRoute,
})).listen(3000);
```

Routes are recorded as **templates** (`/api/users/:id`, router prefixes included), never raw
paths; requests no route matched become `__unmatched__`, so 404 scanners can't flood your data.

## Who is calling, and what it costs

```js
import { recordLlmResponse, recordLlmUsage } from "reqly-node";

reqlyExpress({ consumerHeader: "x-api-key", consumerSalt: process.env.REQLY_CONSUMER_SALT });

const completion = await openai.chat.completions.create({ model: "gpt-4o-mini", messages });
recordLlmResponse(completion);              // OpenAI / Anthropic responses, or:
recordLlmUsage("gpt-4o-mini", 1200, 240);
```

Consumer ids are HMAC-SHA256-hashed with your salt before they leave the process; the
collector never sees an API key. LLM usage follows the request through `AsyncLocalStorage`,
so it works after any number of `await`s.

## Your OpenAPI spec, uploaded for you

```js
await app.register(reqlyFastify({ serviceName: "checkout-api", pushOpenapi: true }));  // @fastify/swagger
reqlyNest(app, { serviceName: "checkout-api", pushOpenapi: SwaggerModule.createDocument(app, config) });
reqlyExpress({ serviceName: "checkout-api", pushOpenapi: () => spec });                 // any spec
```

Sent once, on the first request; the dashboard then shows where your spec and your real traffic disagree.

## An alert looks like this

```
🔴 Anomaly — flask-demo /orders (Friday 15:00-16:00 UTC, z=5.37)
• error rate 30.0% vs 2.2% usual · p95 6588ms vs 1576ms usual
• running release v2 — vs v1: errors 2.6% → 33.1%, p95 2072ms → 4501ms
• 100% of errors came from host pod-3, which served 23% of requests
```

## Configuration

Options passed to the middleware (or to a shared `new ReqlyClient(options)`), else environment
variables, else defaults:

| Option | Environment variable | Default |
|---|---|---|
| `serviceName` | `REQLY_SERVICE_NAME` | `npm_package_name`, else `unnamed-service` |
| `collectorUrl` | `REQLY_COLLECTOR_URL` | `http://localhost:8000` |
| `apiKey` | `REQLY_API_KEY` | none |
| `release` | `REQLY_RELEASE`, then CI variables | auto-detected |
| `environment` | `REQLY_ENVIRONMENT` | none |
| `sampleRate` | `REQLY_SAMPLE_RATE` | `1` |
| `flushIntervalMs` | `REQLY_FLUSH_INTERVAL_MS` (or `_SECONDS`) | `5000`; a full batch is sent at once |
| `maxBatchSize` / `maxQueueSize` | `REQLY_MAX_BATCH_SIZE` / `REQLY_MAX_QUEUE_SIZE` | `200` / `2000` |
| `ignoreRoutes` | `REQLY_IGNORE_ROUTES` | `/health,/metrics` |
| `consumerHeader` / `consumer` | `REQLY_CONSUMER_HEADER` / — | none |
| `consumerSalt` / `hashConsumer` | `REQLY_CONSUMER_SALT` / `REQLY_HASH_CONSUMER` | none / `true` |
| `pushOpenapi` | `REQLY_PUSH_OPENAPI` | `false` |

Events still queued when the process exits on its own are sent automatically; on `SIGTERM`
or before `process.exit()`, `await reqly.client.shutdown()` first.

## Built to stay out of your way

- ⚡ **~6 µs per request** on Hono, ~10–13 µs on Fastify, ~22–27 µs on Express ([benchmark](https://reqly.tanisheesh.in/docs/reference/benchmarks/))
- 🛡️ **Fail-open:** nothing the SDK does can throw into your request path; an internal error turns instrumentation off and logs once
- 🧵 **Off the request path:** events are batched in memory and sent by a timer that never keeps your process alive
- 📦 **Bounded:** a fixed-size queue (oldest dropped first) and route templates only
- 🔁 **Safe retries** on 408, 429, 5xx and network errors, deduplicated by the collector
- 📏 **Streamed responses measured:** server-sent events and LLM token streams get their real size (Express, Fastify, Koa, NestJS, node:http)

## Compatibility

| | |
|---|---|
| Runtimes | Node.js 20+, Bun. Edge runtimes (Cloudflare Workers): use [OpenTelemetry](https://reqly.tanisheesh.in/docs/instrument/opentelemetry/) |
| Frameworks | Express 4/5 · Fastify 4/5 · Hono · Koa 2/3 · NestJS · node:http |
| Modules | ESM and CommonJS, TypeScript types included |
| Collector | consumer and LLM views need 0.8.0+, `pushOpenapi` 0.7.0+ |

On Python too? There's a [Python SDK](https://pypi.org/project/reqly/), and any language can
report through [OpenTelemetry](https://reqly.tanisheesh.in/docs/instrument/opentelemetry/).

## Run the collector

The SDK sends to a Reqly collector you host (TimescaleDB + collector + dashboard):

```bash
git clone https://github.com/tanisheesh/reqly && cd reqly && docker compose up -d
# or on Kubernetes
helm install reqly oci://ghcr.io/tanisheesh/charts/reqly -n reqly --create-namespace
```

[Quickstart](https://reqly.tanisheesh.in/docs/quickstart/) ·
[Deploy to production](https://reqly.tanisheesh.in/docs/self-hosting/deploy/) ·
[Changelog](https://github.com/tanisheesh/reqly/blob/main/sdk-node/CHANGELOG.md) ·
License: [GPL-3.0](https://github.com/tanisheesh/reqly/blob/main/LICENSE)

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
