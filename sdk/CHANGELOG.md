# Changelog

All notable changes to the `reqly` Python SDK.

## 0.5.1 — 2026-10-10

### Fixed
- A `route_resolver` that raised (with `instrument_asgi()`) propagated the exception into the
  app after the response was sent. It now counts as "no route" (`__unmatched__`), and recording
  in the ASGI and Flask integrations is guarded like the rest of the SDK: nothing in it can
  raise into a request.

## 0.5.0 — 2026-10-10

### Added
- **Consumers:** `consumer_header="X-API-Key"` (or `REQLY_CONSUMER_HEADER`) or
  `consumer=callable` identifies the caller of each request. Ids are HMAC-SHA256-hashed
  with `consumer_salt` (`REQLY_CONSUMER_SALT`) in the SDK, so raw keys never leave the app;
  `hash_consumer=False` sends them as they are (e.g. tenant names).
- **LLM usage:** `reqly.record_llm_usage(model, input_tokens, output_tokens)` inside a
  request handler, or `reqly.record_llm_response(response)` with an OpenAI- or
  Anthropic-style response. The collector (0.8.0+) turns it into cost per route.
- **Any WSGI / ASGI app:** `reqly.instrument_wsgi(app, route_resolver=...)` and
  `reqly.instrument_asgi(app, route_resolver=...)` return the wrapped app, for frameworks
  without a dedicated integration (Bottle, Pyramid, Falcon, ...).
- Django: `REQLY` settings accept the consumer options.

## 0.4.0 — 2026-10-10

### Added
- `push_openapi=True` (or `REQLY_PUSH_OPENAPI=true`): FastAPI and Litestar apps upload
  their OpenAPI spec to the collector on the first request (once per process, on a
  background thread, failures only logged). Collector 0.7.0+ compares it with the traffic
  and shows undocumented, unused and deprecated-but-used endpoints.

## 0.3.0 — 2026-10-10

### Added
- **Django** (incl. Django REST Framework and Django Ninja): add
  `reqly.integrations.django.ReqlyMiddleware` to `MIDDLEWARE`, optional `REQLY = {...}`
  settings. Sync- and async-capable. Routes are normalized to the `{param}` style used by
  the other integrations (`users/<int:pk>/` and `^users/(?P<pk>[^/.]+)/$` → `/users/{pk}/`).
- **Litestar**: `reqly.instrument(app)`; the route comes from Litestar's `path_template`.
- Extras: `reqly[django]`, `reqly[litestar]`, `reqly[starlette]`.

### Fixed
- Plain **Starlette** apps recorded every request as `__unmatched__` (only FastAPI puts the
  matched route in the ASGI scope on older Starlette versions). Routes are now resolved,
  including inside `Mount`.
- Routes inside mounted sub-apps (FastAPI `app.mount()`, Starlette `Mount`) were recorded
  without the mount prefix (`/items/{id}` instead of `/api/items/{id}`).
- Subclasses of app classes (`class App(FastAPI)`) are detected by framework, and a repeat
  `instrument()` call is recognized without setting attributes on the app (Litestar apps
  use `__slots__`).

## 0.2.0 — 2026-10-09

First release since 0.1.4: also contains the fixes prepared as 0.1.5, which was never
published to PyPI.

### Added
- `release` and `environment` options (`REQLY_RELEASE` / `REQLY_ENVIRONMENT`). `release` is
  auto-detected from common CI/hosting variables (`GITHUB_SHA`, `RENDER_GIT_COMMIT`,
  `VERCEL_GIT_COMMIT_SHA`, …) and sent once per batch. Needs collector 0.3.0 to be stored;
  older collectors ignore it.
- `request_bytes` / `response_bytes` on every event. FastAPI counts the actual ASGI body
  messages (works for streaming); Flask reports `Content-Length` (`None` when streamed).

### Fixed
- Batches are now retried on `408`, `429` and any `5xx` response instead of being
  dropped on the first server error. Other `4xx` responses are still dropped immediately.
- Pre-fork servers (gunicorn `--preload`, uWSGI without lazy-apps): forked workers now
  restart the flush thread and open their own HTTP connections. Previously their events
  queued forever and were never shipped.
- Calling `reqly.instrument()` twice on the same app no longer adds a second middleware
  and double-counts every request; the existing client is returned.
- `REQLY_IGNORE_ROUTES` tolerates whitespace and empty entries (`"/health, /metrics"`).

### Changed
- The SDK version is read from package metadata in one place. A source checkout that
  was never installed reports `0.0.0+unknown` instead of a stale hardcoded number.
- Clarified the sampling docs: with `sample_rate < 1.0`, dashboard request counts show
  the sampled volume (the collector does not scale them back up).

## 0.1.4

- Last release before this changelog was started.
