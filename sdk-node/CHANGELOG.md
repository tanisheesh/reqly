# Changelog

All notable changes to `reqly-node`.

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
