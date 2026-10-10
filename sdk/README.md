<p align="center">
  <a href="https://reqly.tanisheesh.in"><img src="https://reqly.tanisheesh.in/docs/assets/logo.svg" width="64" height="64" alt="Reqly"></a>
</p>

<h1 align="center">reqly</h1>

<p align="center">
  <strong>API monitoring for Python that tells you what broke, when, and which deploy did it.</strong><br>
  FastAPI · Flask · Django · Starlette · Litestar · any WSGI/ASGI app, in one line.
</p>

<p align="center">
  <a href="https://pypi.org/project/reqly/"><img src="https://img.shields.io/pypi/v/reqly?color=06b6d4&style=flat-square&label=pypi" alt="PyPI version"></a>
  <a href="https://pypi.org/project/reqly/"><img src="https://img.shields.io/pypi/pyversions/reqly?color=06b6d4&style=flat-square" alt="Python versions"></a>
  <a href="https://pypi.org/project/reqly/"><img src="https://img.shields.io/pypi/dm/reqly?color=06b6d4&style=flat-square&label=downloads" alt="Downloads"></a>
  <a href="https://github.com/tanisheesh/reqly/blob/main/sdk/LICENSE"><img src="https://img.shields.io/badge/license-MIT-06b6d4?style=flat-square" alt="License: MIT"></a>
</p>

<p align="center">
  <a href="https://reqly.tanisheesh.in/docs/instrument/python/"><strong>Documentation</strong></a> ·
  <a href="https://reqly.tanisheesh.in/docs/quickstart/">Quickstart</a> ·
  <a href="https://reqly-eventflow-dashboard.onrender.com">Live demo</a> ·
  <a href="https://github.com/tanisheesh/reqly">GitHub</a>
</p>

---

## Install

```bash
pip install reqly
```

```python
import reqly
from fastapi import FastAPI

app = FastAPI()
reqly.instrument(app, service_name="checkout-api",
                 collector_url="https://reqly.example.com", api_key="your-ingest-key")
```

That's it: every route now reports its latency, errors, status codes and release to your
own [Reqly collector](https://github.com/tanisheesh/reqly). No decorators, no agent, no
vendor account.

## What you get

| | |
|---|---|
| 📈 **Real percentiles** | p50 / p95 / p99 per route and per service, merged exactly across routes and time |
| 🚀 **Deploy-aware** | The release is picked up from your CI or host (`GITHUB_SHA`, `RENDER_GIT_COMMIT`, …); every alert says which release was running |
| 🔔 **Alerts that explain** | Hourly checks against each route's weekday × hour baseline, naming the host, error type and clients behind a spike; to Slack, Discord or a webhook |
| 💬 **Ask Reqly** | *"Why did /orders start failing?"* answered from your own data, every number checked |
| 🎯 **SLOs** | Availability and latency objectives with error budgets and burn-rate alerts |
| 🧾 **OpenAPI drift** | Undocumented, unused and deprecated-but-called endpoints (`push_openapi=True`) |
| 👥 **API consumers** | Who calls your API and who an incident hit, with ids hashed in the SDK |
| 💸 **LLM cost per route** | Tokens and cost per route and model, and an alert when it spikes |

## Your framework

| Framework | What to write |
|---|---|
| FastAPI, Starlette, Litestar, Flask | `reqly.instrument(app, service_name="...")`, detected automatically |
| Django, DRF, Django Ninja | `"reqly.integrations.django.ReqlyMiddleware"` first in `MIDDLEWARE` |
| Bottle, Pyramid, Falcon, any WSGI app | `app = reqly.instrument_wsgi(app, route_resolver=...)` |
| Any ASGI app | `app = reqly.instrument_asgi(app, route_resolver=...)` |

```python
# settings.py (Django)
MIDDLEWARE = ["reqly.integrations.django.ReqlyMiddleware", ...]
REQLY = {"service_name": "checkout-api", "api_key": "your-ingest-key"}

# Bottle: tell Reqly where the route template is
app = reqly.instrument_wsgi(app, service_name="checkout-api",
                            route_resolver=lambda environ: environ["bottle.route"].rule)
```

Routes are recorded as **templates** (`/users/{id}`), never raw paths; requests no route
matched become `__unmatched__`, so 404 scanners can't flood your data.

## Who is calling, and what it costs

```python
reqly.instrument(app, consumer_header="X-API-Key", consumer_salt=os.environ["REQLY_CONSUMER_SALT"])

completion = client.chat.completions.create(model="gpt-4o-mini", messages=messages)
reqly.record_llm_response(completion)          # OpenAI / Anthropic responses, or:
reqly.record_llm_usage("gpt-4o-mini", input_tokens=1200, output_tokens=240)
```

Consumer ids are HMAC-SHA256-hashed with your salt before they leave the app; the collector
never sees an API key.

## An alert looks like this

```
🔴 Anomaly — flask-demo /orders (Friday 15:00-16:00 UTC, z=5.37)
• error rate 30.0% vs 2.2% usual · p95 6588ms vs 1576ms usual
• running release v2 — vs v1: errors 2.6% → 33.1%, p95 2072ms → 4501ms
• 100% of errors came from host pod-3, which served 23% of requests
```

## Configuration

Pass options to `instrument()` or set environment variables (argument → environment variable → default).

| Argument | Environment variable | Default |
|---|---|---|
| `service_name` | `REQLY_SERVICE_NAME` | the script name |
| `collector_url` | `REQLY_COLLECTOR_URL` | `http://localhost:8000` |
| `api_key` | `REQLY_API_KEY` | `None` |
| `release` | `REQLY_RELEASE`, then CI variables | auto-detected |
| `environment` | `REQLY_ENVIRONMENT` | `None` |
| `sample_rate` | `REQLY_SAMPLE_RATE` | `1.0` |
| `flush_interval_seconds` | `REQLY_FLUSH_INTERVAL_SECONDS` (or `_MS`) | `5.0`; a full batch is sent at once |
| `max_batch_size` / `max_queue_size` | `REQLY_MAX_BATCH_SIZE` / `REQLY_MAX_QUEUE_SIZE` | `200` / `2000` |
| `ignore_routes` | `REQLY_IGNORE_ROUTES` | `/health,/metrics` |
| `consumer_header` / `consumer` | `REQLY_CONSUMER_HEADER` / — | `None` |
| `consumer_salt` / `hash_consumer` | `REQLY_CONSUMER_SALT` / `REQLY_HASH_CONSUMER` | `None` / `True` |
| `push_openapi` | `REQLY_PUSH_OPENAPI` | `False` (FastAPI, Litestar) |

Every option, explained: [Python SDK docs](https://reqly.tanisheesh.in/docs/instrument/python/#configuration).

## Built to stay out of your way

- ⚡ **~13 µs per request** on FastAPI and Starlette, ~33 µs on Flask ([benchmark](https://reqly.tanisheesh.in/docs/reference/benchmarks/))
- 🛡️ **Fail-open:** an internal error is logged once and instrumentation turns itself off; it never raises into your app
- 🧵 **Off the request path:** a background thread ships batches with strict timeouts, so a slow collector never blocks a request
- 📦 **Bounded:** a fixed-size queue (oldest dropped first) and route templates only
- 🔁 **Safe retries** on 408, 429 and 5xx, deduplicated by the collector
- 🍴 **Pre-fork servers** (gunicorn `--preload`, uWSGI) restart the shipper in each worker

## Compatibility

| | |
|---|---|
| Python | 3.9 – 3.13 |
| FastAPI | 0.100+, including `app.mount()`ed sub-apps |
| Starlette · Litestar · Flask | 0.27+ · 2.0+ · 2.3+ |
| Django | 4.2+, sync and async views; DRF and Django Ninja |
| Collector | consumer and LLM views need 0.8.0+, `push_openapi` 0.7.0+ |

Not on Python? There's a [Node.js SDK](https://www.npmjs.com/package/reqly-node), and any
language can report through [OpenTelemetry](https://reqly.tanisheesh.in/docs/instrument/opentelemetry/).

## Run the collector

The SDK sends to a Reqly collector you host (TimescaleDB + collector + dashboard):

```bash
git clone https://github.com/tanisheesh/reqly && cd reqly && docker compose up -d
# or on Kubernetes
helm install reqly oci://ghcr.io/tanisheesh/charts/reqly -n reqly --create-namespace
```

[Quickstart](https://reqly.tanisheesh.in/docs/quickstart/) ·
[Deploy to production](https://reqly.tanisheesh.in/docs/self-hosting/deploy/) ·
[Changelog](https://github.com/tanisheesh/reqly/blob/main/sdk/CHANGELOG.md) ·
License: [MIT](https://github.com/tanisheesh/reqly/blob/main/sdk/LICENSE) (the collector is AGPL-3.0)

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
