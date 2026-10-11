---
title: Python SDK
description: Instrument FastAPI, Flask, Django, Starlette, Litestar or any WSGI/ASGI app with the reqly package.
---

# Python SDK

```bash
pip install reqly
```

Python 3.9–3.13. One runtime dependency (`httpx`). [PyPI](https://pypi.org/project/reqly/) · [Changelog](https://github.com/tanisheesh/reqly/blob/main/sdk/CHANGELOG.md)

## Instrument your framework

`reqly.instrument(app)` detects the framework itself. There are no decorators and no middleware to wire up.

=== "FastAPI"

    ```python
    import reqly
    from fastapi import FastAPI

    app = FastAPI()
    reqly.instrument(
        app,
        service_name="checkout-api",
        collector_url="https://reqly.example.com",
        api_key="your-ingest-key",
    )
    ```

    Routes in `app.mount()`ed sub-apps are included.

=== "Flask"

    ```python
    import reqly
    from flask import Flask

    app = Flask(__name__)
    reqly.instrument(app, service_name="checkout-api")  # other settings from REQLY_* env vars
    ```

=== "Starlette / Litestar"

    ```python
    reqly.instrument(app, service_name="checkout-api")
    ```

=== "Django"

    Django has no app object, so add the middleware **first** in `MIDDLEWARE`. This also works with Django REST Framework and Django Ninja.

    ```python
    # settings.py
    MIDDLEWARE = [
        "reqly.integrations.django.ReqlyMiddleware",
        # ...
    ]
    REQLY = {"service_name": "checkout-api", "api_key": "your-ingest-key"}  # optional
    ```

=== "Any WSGI / ASGI app"

    For Bottle, Pyramid, Falcon, CherryPy or a bare ASGI app, wrap the app and serve the result. Only the framework knows the route template, so pass a `route_resolver`. Without one, every request is recorded as `__unmatched__`, never as a raw path.

    ```python
    app = reqly.instrument_wsgi(
        app, service_name="checkout-api",
        route_resolver=lambda environ: environ["bottle.route"].rule,  # Bottle
    )
    # Pyramid: environ["bfg.routes.route"].pattern
    # ASGI:    reqly.instrument_asgi(app, route_resolver=...)
    ```

Routes are recorded as templates in one style across frameworks: Django's `users/<int:pk>/` and DRF's `^users/(?P<pk>[^/.]+)/$` both become `/users/{pk}/`.

## What is recorded

For each request:

- method
- **route template** (`/orders/{id}`, never `/orders/123`)
- status code and duration
- error type
- host
- **release** and environment
- request and response body sizes
- optionally, a hashed [consumer id](../features/consumers.md) and [LLM token usage](../features/llm-cost.md)

Request bodies, query strings and headers are never sent.

**Releases are detected automatically** from `REQLY_RELEASE` or your CI or host: `GITHUB_SHA`, `CI_COMMIT_SHA`, `RENDER_GIT_COMMIT`, `VERCEL_GIT_COMMIT_SHA`, `RAILWAY_GIT_COMMIT_SHA`, `HEROKU_SLUG_COMMIT`, `K_REVISION` and others. Deploys then show up in Reqly with no extra code.

## Who is calling

Tag each request with its API consumer. The id is hashed with HMAC-SHA256 and your secret salt before it leaves the app, so raw keys never reach the collector.

```python
reqly.instrument(app, consumer_header="X-API-Key", consumer_salt=os.environ["REQLY_CONSUMER_SALT"])

# or any logic, from a RequestInfo:
reqly.instrument(app, consumer=lambda info: info.headers.get("x-tenant-id"))
```

See [API consumers](../features/consumers.md).

## LLM cost per route

Record token usage where you call a model. The collector prices it per route.

```python
completion = client.chat.completions.create(model="gpt-4o-mini", messages=messages)
reqly.record_llm_response(completion)   # OpenAI- or Anthropic-style responses, or:
reqly.record_llm_usage("gpt-4o-mini", input_tokens=1200, output_tokens=240)
```

See [LLM cost](../features/llm-cost.md).

## Upload your OpenAPI spec

FastAPI and Litestar generate their own spec. `push_openapi=True` uploads it on the first request, so the dashboard can compare it with real traffic ([OpenAPI drift](../features/openapi-drift.md)).

```python
reqly.instrument(app, push_openapi=True)
```

## Configuration

Every option can be passed to `instrument()` or set as an environment variable. Resolution order: **argument → environment variable → default**.

| Argument | Environment variable | Default |
|---|---|---|
| `service_name` | `REQLY_SERVICE_NAME` | `sys.argv[0]` basename |
| `collector_url` | `REQLY_COLLECTOR_URL` | `http://localhost:8000` |
| `api_key` | `REQLY_API_KEY` | `None` |
| `release` | `REQLY_RELEASE`, then CI variables | auto-detected, else `None` |
| `environment` | `REQLY_ENVIRONMENT` | `None` |
| `sample_rate` | `REQLY_SAMPLE_RATE` | `1.0` |
| `flush_interval_seconds` | `REQLY_FLUSH_INTERVAL_SECONDS` (or `REQLY_FLUSH_INTERVAL_MS`) | `5.0` (at least `0.1`); a full batch is sent at once |
| `max_batch_size` | `REQLY_MAX_BATCH_SIZE` | `200` (kept within 1–1,000, the collector's limit) |
| `max_queue_size` | `REQLY_MAX_QUEUE_SIZE` | `2000` (at least `1`) |
| `ignore_routes` | `REQLY_IGNORE_ROUTES` (comma-separated) | `/health,/metrics` |
| `consumer_header` | `REQLY_CONSUMER_HEADER` | `None`, e.g. `X-API-Key` |
| `consumer` | — | `None`: `callable(RequestInfo) -> str | None` |
| `consumer_salt` | `REQLY_CONSUMER_SALT` | `None`: set it |
| `hash_consumer` | `REQLY_HASH_CONSUMER` | `True`. `False` sends ids unhashed (only for non-secret ids) |
| `push_openapi` | `REQLY_PUSH_OPENAPI` | `False` (FastAPI, Litestar) |

With `sample_rate` below 1.0, request counts in the dashboard are the sampled volume; latency percentiles and error rates stay unbiased.

## Guarantees

- **Small overhead:** about 13 µs per request on FastAPI and Starlette and 33 µs on Flask ([benchmark](../reference/benchmarks.md)).
- **Fail-open:** any internal SDK error is caught and logged once. Instrumentation disables itself rather than raise into your app.
- **Non-blocking:** shipping happens on a background thread with strict HTTP timeouts, so a slow or unreachable collector never blocks a request.
- **Bounded cardinality:** route templates only. Unmatched paths (404s, scanners) collapse into one `__unmatched__` bucket.
- **Bounded memory:** a fixed-size queue. Under backpressure the oldest events are dropped and counted.
- **Safe retries:** batches are retried with exponential backoff on `408`, `429` and `5xx`. Other `4xx` responses are dropped. Every event carries an `event_id` the collector deduplicates on, so a retry never double-counts.
- **Bounded exit:** queued events are sent when the process exits, with one attempt per batch and at most 5 seconds in total, so a collector outage never holds up a deploy or a worker restart. What couldn't be sent is counted as dropped.
- **Abandoned requests:** a request cancelled before the app answered (the client disconnected, or the server is shutting down) is recorded as `499` (client closed request), not as a `500`.
- **Pre-fork servers:** under gunicorn `--preload` (or uWSGI without lazy-apps), each worker restarts its own flush thread and connection pool.

## Compatibility

| | Supported |
|---|---|
| Python | 3.9 – 3.13 |
| FastAPI | 0.100+, including routes in `app.mount()`ed sub-apps |
| Starlette | 0.27+, including `Mount` |
| Litestar | 2.0+ |
| Flask | 2.3+ |
| Django | 4.2+, sync and async views; DRF and Django Ninja |
| Other WSGI / ASGI | any, with `instrument_wsgi()` / `instrument_asgi()` and a `route_resolver` |
| Collector | any version. `release`, `environment` and body sizes need 0.3.0+; `push_openapi` needs 0.7.0+; consumer and LLM views need 0.8.0+ |
