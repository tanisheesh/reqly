# Changelog

All notable changes to `reqly-node`.

## 0.3.1 — 2026-10-11

### Changed
- License: **MIT** (was GPL-3.0-only), so the SDK can be used in any app, open or closed.
  The Reqly collector and dashboard are AGPL-3.0. No code changes.

## 0.3.0 — 2026-10-11

### Added
- `reqlyHttp(handler, { routeResolver })`: wraps a plain `(req, res)` handler for node:http or
  a framework without a built-in integration, like the Python SDK's `instrument_wsgi`. Without
  a resolver (or when it throws) requests are `__unmatched__`, never the raw path; errors the
  handler throws or rejects with are recorded with their type and rethrown.
- Verified on Bun: Hono on `Bun.serve` and `reqlyHttp` on Bun's node:http, run in CI.
- `pushOpenapi`: uploads the app's OpenAPI spec once, on the first request, for API drift: the
  spec object (e.g. NestJS `SwaggerModule.createDocument`), a function returning it, or `true`
  to read it from @fastify/swagger. Also `REQLY_PUSH_OPENAPI`.
- Streamed response bodies (no Content-Length: streams, chunked, server-sent events) are
  measured by counting what is written, in Express, Fastify, Koa, NestJS and `reqlyHttp`.
- `stats.observed`: requests seen before sampling; retries get jitter.
- `REQLY_FLUSH_INTERVAL_SECONDS` (the Python SDK's name) is read too.

### Fixed
- Express and Fastify counted any error as an error, so `next(createError(404))` or a 400
  validation error raised the error rate; now, like Koa, NestJS and the Python SDK, only errors
  that end in a 5xx are errors.
- Koa recorded a request before Koa sent a stream body, so its time and size were missing;
  it now records when the response finishes.

## 0.2.0 — 2026-10-10

### Added
- `reqlyKoa()`: Koa 2 and 3 middleware. Routes are @koa/router templates with router
  prefixes; a thrown error is recorded with its status (500 unless it carries one, like
  `ctx.throw(404)`) and, for 5xx, its type.
- `reqlyNest(app)`: NestJS on the Express or Fastify adapter. Routes include the global
  prefix and controller path, Nest's catch-all 404 is `__unmatched__`, and a global
  interceptor records the type of exceptions that become 5xx (HttpExceptions below 500 are
  not errors). No dependency on @nestjs packages or rxjs.

### Changed
- The CommonJS build uses `moduleResolution: bundler` (TypeScript 7 removed `node10`).

## 0.1.2 — 2026-10-11

### Fixed
- Under steady traffic every request caused its own HTTP call to the collector: a flush kept
  sending until the queue was empty, so events arriving during a send went out one per
  batch. A flush now sends what was queued when it started (Express overhead: +330 µs →
  +27 µs per request), and `shutdown()` keeps flushing until the queue is empty.
- Consumer ids are hashed once per distinct value.

## 0.1.1 — 2026-10-10

### Fixed
- Hono: routes registered with `app.all()` were recorded as `__unmatched__`. The route is now
  the handler Hono actually ran; requests that only reached a wildcard middleware are
  `__unmatched__`.
- A Reqly middleware registered twice (or two clients) recorded every request twice; the first
  one that sees a request now owns it.
- Events still queued when the process exits on its own are sent (`beforeExit`, like the Python
  SDK's `atexit`). On signals or `process.exit()`, call `client.shutdown()`.

## 0.1.0 — 2026-10-10

First release.

- Express (4, 5), Fastify (4, 5) and Hono middleware recording route templates, status,
  duration, error type, request/response size, release and environment.
- Consumers from a header or a function, HMAC-SHA256-hashed in the SDK.
- `recordLlmUsage()` / `recordLlmResponse()` for LLM cost per route.
- Batched, bounded, fail-open shipping with retries; ESM and CommonJS builds.

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
