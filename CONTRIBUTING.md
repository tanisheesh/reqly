# Reqly — Self-hosting & Developer Guide

## Quick start

```bash
git clone https://github.com/tanisheesh/reqly
cd reqly
cp .env.example .env
```

Edit `.env`:

```env
POSTGRES_PASSWORD=your_password
REQLY_INGEST_KEY=your_ingest_key
REQLY_READ_KEY=your_read_key
GROQ_API_KEY=gsk_...        # optional — skip for plain-text insights
```

Start the stack:

```bash
docker compose up -d
```

| URL | What |
|---|---|
| http://localhost:5173 | Dashboard |
| http://localhost:8000 | Collector API |
| http://localhost:8000/docs | Interactive API docs |

```bash
docker compose down       # stop, keep data
docker compose down -v    # stop + wipe database
```

---

## Instrument your app

```bash
pip install reqly
```

**FastAPI**
```python
import reqly
from fastapi import FastAPI

app = FastAPI()
reqly.instrument(app, service_name="my-api",
                     collector_url="http://localhost:8000",
                     api_key="your_ingest_key")
```

**Flask**
```python
import reqly
from flask import Flask

app = Flask(__name__)
reqly.instrument(app, service_name="my-api",
                     collector_url="http://localhost:8000",
                     api_key="your_ingest_key")
```

Same call for both — `instrument()` auto-detects the framework.

---

## Configuration reference

| kwarg | env var | default | description |
|---|---|---|---|
| `service_name` | `REQLY_SERVICE_NAME` | script name | Label shown in dashboard |
| `collector_url` | `REQLY_COLLECTOR_URL` | `http://localhost:8000` | Where to ship data |
| `api_key` | `REQLY_API_KEY` | `None` | Ingest key (`X-Reqly-Key` header) |
| `sample_rate` | `REQLY_SAMPLE_RATE` | `1.0` | Fraction of requests to capture |
| `flush_interval_seconds` | `REQLY_FLUSH_INTERVAL_SECONDS` | `5.0` | How often to ship a batch |
| `max_batch_size` | `REQLY_MAX_BATCH_SIZE` | `200` | Events per batch |
| `max_queue_size` | `REQLY_MAX_QUEUE_SIZE` | `2000` | In-memory queue cap |
| `ignore_routes` | `REQLY_IGNORE_ROUTES` | `/health,/metrics` | Routes to skip |
| `capture_request_body` | `REQLY_CAPTURE_REQUEST_BODY` | `False` | Include request body (not implemented yet) |
| `release` | `REQLY_RELEASE` | auto-detected from CI vars | Git SHA / version — enables deploy-aware insights |
| `environment` | `REQLY_ENVIRONMENT` | `None` | e.g. `prod`, `staging` |

---

## Project structure

```
reqly/
├── sdk/                    # pip install reqly
│   └── reqly/
│       ├── __init__.py     # reqly.instrument() — public API
│       ├── core/           # client, config, buffer, shipper, sampling
│       └── integrations/   # fastapi.py, flask.py
│
├── collector/              # FastAPI ingest + query + insights backend
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── auth.py
│   │   ├── routers/        # ingest, metrics, insights
│   │   ├── insights/       # anomaly detection, groq client, scheduler
│   │   └── db/
│   └── migrations/
│       └── 001_init.sql    # hypertable + continuous aggregates + retention
│
├── dashboard/              # React 19 + Vite + Tailwind + recharts
│
├── infra/
│   ├── ec2-userdata.sh     # EC2 bootstrap (TimescaleDB on Docker)
│   ├── DEPLOY.md           # AWS production deployment guide
│   └── sam/                # AWS Lambda + EventBridge + S3 (weekly insights)
│
├── docker-compose.yml
└── .env.example
```

---

## Running tests

**SDK**
```bash
cd sdk
pip install -e ".[dev]"
pytest tests -v
```

**Collector** (needs TimescaleDB running)
```bash
docker compose up timescaledb -d
cd collector
pip install -e ".[dev]"
pytest tests -v
```

**Dashboard type-check**
```bash
cd dashboard
npm install
npx tsc --noEmit
```

---

## AWS (optional — weekly AI insights as Lambda)

The collector already runs a weekly insights job internally via APScheduler. AWS Lambda is an optional alternative that runs the same job as a proper cloud-native cron.

```bash
cd infra/sam
sam build
sam deploy --guided
```

SAM will ask for `DatabaseUrl`, `GroqApiKey` and `Environment` (use `production` unless you want an unauthenticated trigger URL). If you deploy it, set `INSIGHTS_SCHEDULER_ENABLED=false` on the collector so the job doesn't run twice. See [infra/DEPLOY.md](infra/DEPLOY.md) for the full production setup.

---

## Releasing the SDK

The Python SDK is published to PyPI by `.github/workflows/release-sdk.yml` using
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) — GitHub proves its
identity to PyPI with a short-lived OIDC token, so no PyPI API token is stored anywhere.

1. Bump `version` in `sdk/pyproject.toml` (e.g. `0.2.1`).
2. Add a `## 0.2.1 — YYYY-MM-DD` section at the top of `sdk/CHANGELOG.md`.
3. Merge to `main`, then tag that commit and push the tag:
   ```bash
   git tag sdk-v0.2.1
   git push origin sdk-v0.2.1
   ```

The workflow refuses to publish if the tag doesn't match `pyproject.toml`, the changelog
has no entry for the version, or the version is already on PyPI. It runs the SDK tests,
builds and checks the package, publishes it, then creates a GitHub release with the
changelog section as notes. To dry-run everything except publishing, start the workflow
manually from the Actions tab (**Run workflow**).

**One-time setup** (already done for this repo): a `pypi` environment in the GitHub repo
settings, restricted to `sdk-v*` tags, and a trusted publisher on
[pypi.org → reqly → Publishing](https://pypi.org/manage/project/reqly/settings/publishing/)
with owner `tanisheesh`, repository `reqly`, workflow `release-sdk.yml`, environment `pypi`.

## Contributing

1. Fork → branch from `main`
2. Run tests before and after
3. Open a PR — describe what changed and why

For new framework integrations: create `sdk/reqly/integrations/<framework>.py` with `instrument_<framework>(app, client: ReqlyClient)`. See `fastapi.py` and `flask.py` for the pattern.
