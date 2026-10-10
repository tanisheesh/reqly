# Changelog

All notable changes to `reqly-node`.

## 0.1.0 — 2026-10-10

First release.

- Express (4, 5), Fastify (4, 5) and Hono middleware recording route templates, status,
  duration, error type, request/response size, release and environment.
- Consumers from a header or a function, HMAC-SHA256-hashed in the SDK.
- `recordLlmUsage()` / `recordLlmResponse()` for LLM cost per route.
- Batched, bounded, fail-open shipping with retries; ESM and CommonJS builds.
