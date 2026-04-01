# reqly

**Open-source API observability SDK for FastAPI and Flask.**  
Auto-instrument your app with 2 lines of code — get latency percentiles, error rates, status distribution, top routes, and weekly AI-generated anomaly reports.

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
reqly.instrument(app, service_name="checkout-api")
# Every route is now tracked — latency · error rate · status codes
```

**Flask**

```python
import reqly
from flask import Flask

app = Flask(__name__)
reqly.instrument(app, service_name="checkout-api")
```

`instrument()` auto-detects FastAPI vs Flask — no other code changes needed.

## What you get

- **p50 / p95 / p99 latency** per route
- **Error rates** and status code distribution (2xx / 3xx / 4xx / 5xx)
- **Top routes** ranked by volume and error rate
- **Live requests/min** tile, time windows 1h / 6h / 24h / 7d
- **Weekly AI anomaly report** — z-score detection with Groq (Llama 3.3-70b) writing the narrative

> "Your `/auth` endpoint degrades every Monday morning (08:00–10:00), with error rate at 18% vs. a 2% baseline and p95 latency at 1850ms vs. 320ms baseline."

## Live Demo

Reqly is running live against [EventFlow](https://eventflow-g2h5.onrender.com), a Flask event management app.

- **Demo app** → [eventflow-g2h5.onrender.com](https://eventflow-g2h5.onrender.com)
- **Metrics dashboard** → [reqly-eventflow-dashboard.onrender.com](https://reqly-eventflow-dashboard.onrender.com)

> Login as **Administrator** (`admin@eventhub.com` / `Admin@123`) → click **Metrics** in the nav.

## Configuration

Every option can be passed as a kwarg to `instrument()` or set via environment variable.  
Resolution order: **kwarg → env var → default**.

| kwarg | env var | default |
|---|---|---|
| `service_name` | `REQLY_SERVICE_NAME` | `sys.argv[0]` basename |
| `collector_url` | `REQLY_COLLECTOR_URL` | `http://localhost:8000` |
| `api_key` | `REQLY_API_KEY` | `None` |
| `sample_rate` | `REQLY_SAMPLE_RATE` | `1.0` |
| `flush_interval_seconds` | `REQLY_FLUSH_INTERVAL_SECONDS` | `5.0` |
| `max_batch_size` | `REQLY_MAX_BATCH_SIZE` | `200` |
| `max_queue_size` | `REQLY_MAX_QUEUE_SIZE` | `2000` |
| `ignore_routes` | `REQLY_IGNORE_ROUTES` (comma-separated) | `/health,/metrics` |
| `capture_request_body` | `REQLY_CAPTURE_REQUEST_BODY` | `False` |

## Design guarantees

**Fail-open** — any internal SDK error is caught and logged once; instrumentation disables itself rather than raise into your app. A slow or unreachable collector never blocks request threads — all shipping happens on a background thread with strict HTTP timeouts.

**Bounded cardinality** — routes are captured as the framework's matched template (`/users/{id}`), never the raw path (`/users/123`). Unmatched paths collapse into a single `__unmatched__` bucket.

**Bounded memory** — events are held in a fixed-size in-memory queue; under backpressure, oldest events are dropped and counted rather than growing unbounded.

## Self-hosting

Reqly is fully self-hostable. Run the full stack (collector + TimescaleDB + dashboard) with Docker Compose:

```bash
git clone https://github.com/tanisheesh/reqly.git
cd reqly
docker compose up -d
```

See the [repository](https://github.com/tanisheesh/reqly) and [CONTRIBUTING.md](https://github.com/tanisheesh/reqly/blob/main/CONTRIBUTING.md) for full setup instructions.

## License

GPL-3.0-or-later — see [LICENSE](https://github.com/tanisheesh/reqly/blob/main/LICENSE).
