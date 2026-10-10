# reqly

**Self-hosted API monitoring for FastAPI, Flask, Django, Starlette and Litestar — two lines of code.**
Latency percentiles, error rates and release tracking for every route, shipped to your own
[Reqly](https://github.com/tanisheesh/reqly) collector — which turns them into deploy-aware
hourly alerts and weekly AI anomaly reports.

[![PyPI](https://img.shields.io/pypi/v/reqly?color=06b6d4&label=reqly)](https://pypi.org/project/reqly/)
[![Python](https://img.shields.io/pypi/pyversions/reqly?color=06b6d4)](https://pypi.org/project/reqly/)
[![License: GPL v3](https://img.shields.io/badge/license-GPL--3.0-06b6d4)](https://github.com/tanisheesh/reqly/blob/main/LICENSE)

---

## Install

```bash
pip install reqly
```

## Usage

**FastAPI**

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
# Every route is now tracked: latency, errors, status codes, release
```

**Flask**

```python
import reqly
from flask import Flask

app = Flask(__name__)
reqly.instrument(app, service_name="checkout-api")  # settings from REQLY_* env vars
```

**Starlette / Litestar** — same call:

```python
reqly.instrument(app, service_name="checkout-api")
```

**Django** (also Django REST Framework and Django Ninja) — Django has no app object, so add
the middleware first in `MIDDLEWARE`:

```python
# settings.py
MIDDLEWARE = [
    "reqly.integrations.django.ReqlyMiddleware",
    # ...
]
REQLY = {"service_name": "checkout-api", "api_key": "your-ingest-key"}  # optional
```

`instrument()` detects the framework by itself — no decorators, no middleware to wire up.
Routes are recorded as templates in one style across frameworks: Django's
`users/<int:pk>/` and DRF's `^users/(?P<pk>[^/.]+)/$` both become `/users/{pk}/`.
The release you're running is picked up automatically from your CI or host
(`GITHUB_SHA`, `RENDER_GIT_COMMIT`, `VERCEL_GIT_COMMIT_SHA`, …), so deploys show up in
Reqly with no extra code.

## What you get

From the SDK, per request: method, **route template** (`/orders/{id}`, never the raw
path), status code, duration, error type, host, **release**, environment and
request/response **body size**.

In the Reqly dashboard and collector:

- **p50 / p95 / p99 latency** per route and per service — real percentiles from
  mergeable sketches, not the max of per-route numbers
- **Error rates, status codes and top routes** over 1h / 6h / 24h / 7d
- **Deploy markers and per-release health** — each release's error rate and p95
- **Hourly alerts** to Slack, Discord or a webhook when a route breaks from its usual
  weekday-hour pattern, with **root-cause hints**
- **API surface vs your OpenAPI spec** — undocumented endpoints that get traffic, documented
  ones nobody calls, and deprecated ones still in use (`push_openapi=True`)
- **Weekly AI report** — statistics find the anomalies, Groq (gpt-oss-120b) writes the
  summary; plain-text fallback without an API key

An alert from the demo data looks like this:

```
🔴 Anomaly — flask-demo /orders (Friday 15:00-16:00 UTC, z=5.37)
• error rate 30.0% vs 2.2% usual · p95 6588ms vs 1576ms usual
• running release v2 — vs v1: errors 2.6% → 33.1%, p95 2072ms → 4501ms
• 100% of errors came from host pod-3, which served 23% of requests
```

**Not on Python?** Node, Java, Go and .NET apps can report to the same collector through
OpenTelemetry — no Reqly SDK needed. See the
[OpenTelemetry guide](https://github.com/tanisheesh/reqly/blob/main/docs/OTEL.md).

## Configuration

Every option can be passed to `instrument()` or set as an environment variable.
Resolution order: **argument → environment variable → default**.

| argument | environment variable | default |
|---|---|---|
| `service_name` | `REQLY_SERVICE_NAME` | `sys.argv[0]` basename |
| `collector_url` | `REQLY_COLLECTOR_URL` | `http://localhost:8000` |
| `api_key` | `REQLY_API_KEY` | `None` |
| `release` | `REQLY_RELEASE`, then CI variables (`GITHUB_SHA`, `CI_COMMIT_SHA`, `RENDER_GIT_COMMIT`, `VERCEL_GIT_COMMIT_SHA`, `RAILWAY_GIT_COMMIT_SHA`, `HEROKU_SLUG_COMMIT`, `K_REVISION`, …) | auto-detected, else `None` |
| `environment` | `REQLY_ENVIRONMENT` | `None` |
| `sample_rate` | `REQLY_SAMPLE_RATE` | `1.0` |
| `flush_interval_seconds` | `REQLY_FLUSH_INTERVAL_SECONDS` | `5.0` |
| `max_batch_size` | `REQLY_MAX_BATCH_SIZE` | `200` |
| `max_queue_size` | `REQLY_MAX_QUEUE_SIZE` | `2000` |
| `ignore_routes` | `REQLY_IGNORE_ROUTES` (comma-separated) | `/health,/metrics` |
| `push_openapi` | `REQLY_PUSH_OPENAPI` | `False` — upload the app's OpenAPI spec (FastAPI, Litestar) on the first request |
| `capture_request_body` | `REQLY_CAPTURE_REQUEST_BODY` | `False` (not implemented yet) |

With `sample_rate` below 1.0, request counts in the dashboard are the sampled volume;
latency percentiles and error rates stay unbiased.

## Design guarantees

**Fail-open** — any internal SDK error is caught and logged once; instrumentation disables
itself rather than raise into your app. A slow or unreachable collector never blocks
request threads — shipping happens on a background thread with strict HTTP timeouts.

**Bounded cardinality** — routes are recorded as the framework's matched template
(`/users/{id}`), never the raw path (`/users/123`). Unmatched paths (404s, scanners)
collapse into a single `__unmatched__` bucket.

**Bounded memory** — events wait in a fixed-size in-memory queue; under backpressure the
oldest events are dropped and counted instead of growing without limit.

**Safe retries** — batches are retried with exponential backoff on `408`, `429` and any
`5xx` (for example a collector restart behind a proxy); other `4xx` responses are dropped
immediately. Every event carries a unique `event_id` the collector deduplicates on, so a
retry never double-counts.

**Pre-fork servers** — under gunicorn `--preload` (or uWSGI without lazy-apps) each
forked worker restarts its own flush thread and HTTP connection pool, so workers' events
are shipped instead of silently queuing forever.

## Compatibility

| | Supported |
|---|---|
| Python | 3.9 – 3.13 |
| FastAPI | 0.100+ (including routes in `app.mount()`ed sub-apps) |
| Starlette | 0.27+ (including `Mount`) |
| Litestar | 2.0+ |
| Flask | 2.3+ |
| Django | 4.2+, sync and async views; DRF and Django Ninja |
| Collector | any version; `release`, `environment` and body sizes are stored by collector 0.3.0+ and ignored by older ones; `push_openapi` needs 0.7.0+ |

## Self-hosting the collector

The SDK sends data to a Reqly collector you run. The full stack — collector,
TimescaleDB and dashboard — starts with Docker Compose:

```bash
git clone https://github.com/tanisheesh/reqly.git
cd reqly
docker compose up -d
```

Setup, configuration and AWS deployment:
[docs/SETUP.md](https://github.com/tanisheesh/reqly/blob/main/docs/SETUP.md) ·
[infra/DEPLOY.md](https://github.com/tanisheesh/reqly/blob/main/infra/DEPLOY.md) ·
[ingest API spec](https://github.com/tanisheesh/reqly/blob/main/docs/INGEST_SPEC.md)

## Live demo

Reqly monitors [EventFlow](https://eventflow-g2h5.onrender.com), a Flask event management
app, in production:

- **Demo app** → [eventflow-g2h5.onrender.com](https://eventflow-g2h5.onrender.com)
- **Metrics dashboard** → [reqly-eventflow-dashboard.onrender.com](https://reqly-eventflow-dashboard.onrender.com)

> Log in as **Administrator** (`admin@eventhub.com` / `Admin@123`) → click **Metrics** in the nav.

## Changelog

See [CHANGELOG.md](https://github.com/tanisheesh/reqly/blob/main/sdk/CHANGELOG.md).

## License

GPL-3.0-or-later — see [LICENSE](https://github.com/tanisheesh/reqly/blob/main/LICENSE).
