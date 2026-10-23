---
title: Deploy to production
description: Run the Reqly collector, dashboard and TimescaleDB on your own servers.
---

# Deploy to production

A production Reqly has three parts:

| Part | What it needs |
|---|---|
| **Database** | TimescaleDB with the Toolkit: the `timescale/timescaledb-ha:pg16` image. A small VM is enough to start |
| **Collector** | One container, `ghcr.io/tanisheesh/reqly-collector`. Run **one** instance: the scheduler, rate limits and caches are per process |
| **Dashboard** | A static site, or the `ghcr.io/tanisheesh/reqly-dashboard` image |

The [live demo](https://reqly-eventflow-dashboard.onrender.com) runs the database on an AWS EC2 t3.micro and the collector and dashboard on Render.

!!! tip "On Kubernetes?"
    The [Helm chart](kubernetes.md) installs all three with one command.

!!! tip "One click"
    Render, Coolify and Railway: see [One-click deploy](one-click.md).

## All-in-one: two containers

`ghcr.io/tanisheesh/reqly` is the collector serving the dashboard on the same port (collector 0.14+). With it a deployment is just that image and TimescaleDB: no separate dashboard, no `CORS_ORIGINS`, and no collector URL to build into the dashboard.

```bash
curl -O https://raw.githubusercontent.com/tanisheesh/reqly/main/deploy/all-in-one/docker-compose.yml
cat > .env <<'ENV'
POSTGRES_PASSWORD=change-me
REQLY_INGEST_KEY=change-me-too
REQLY_READ_KEY=and-this-one
REQLY_ADMIN_PASSWORD=at-least-12-characters
ENV
docker compose up -d     # dashboard and API on http://localhost:8000
```

Put it behind your TLS proxy like any web app. To run the image without Compose, give it the collector settings below; it sets `DASHBOARD_DIR` itself. Any collector can do the same with a built dashboard: point `DASHBOARD_DIR` at the build output (`dashboard/dist`).

The sections below set up the three parts separately.

## 1. Database

Run `timescale/timescaledb-ha:pg16` anywhere you can reach from the collector. Use a strong password and TLS, and connect with `?sslmode=require`.

On AWS, [`infra/ec2-userdata.sh`](https://github.com/tanisheesh/reqly/blob/main/infra/ec2-userdata.sh) sets up an Amazon Linux instance:

- installs Docker
- adds swap
- starts TimescaleDB with a self-signed TLS certificate
- refuses to run with the placeholder password

Step by step: [infra/DEPLOY.md](https://github.com/tanisheesh/reqly/blob/main/infra/DEPLOY.md).

No migration step is needed. The collector applies its schema on start-up and records it in a `schema_migrations` table.

## 2. Collector

```bash
docker run -d -p 8000:8000 \
  -e DATABASE_URL="postgresql://reqly:...@db-host:5432/reqly?sslmode=require" \
  -e REQLY_INGEST_KEY=... \
  -e REQLY_READ_KEY=... \
  -e CORS_ORIGINS=https://reqly-dashboard.example.com \
  ghcr.io/tanisheesh/reqly-collector:0.15.0
```

Images are built for linux/amd64 and arm64. Pin a version rather than `latest`.

Also set:

- **Behind a platform proxy** (Render, Fly.io, a load balancer): `FORWARDED_ALLOW_IPS=*`, so rate limits see real client IPs. Use `*` only if the collector is reachable solely through that proxy.
- `GROQ_API_KEY` (or `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`) for AI features, and alert webhooks.
- `REQLY_ADMIN_PASSWORD` for the first admin: the dashboard needs a sign-in by default. `PUBLIC_DASHBOARD=true` makes it public instead (a demo). See [Projects, keys & sign-in](access.md).

Check it with `GET /v1/health`, which returns `{"status": "ok"}`, and `GET /v1/ready`, which also checks the database. Point Prometheus at `GET /metrics` to watch the collector itself ([settings](configuration.md#monitoring-the-collector)).

!!! tip "Render or similar"
    Create a Web Service from the repository with root directory `collector` and the Docker runtime, or deploy the prebuilt image. Add the variables above.

## 3. Dashboard

=== "Prebuilt image"

    Configured at start-up, so one image works with any collector:

    ```bash
    docker run -d -p 5173:5173 \
      -e REQLY_COLLECTOR_URL=https://reqly.example.com \
      -e REQLY_READ_KEY=... \
      ghcr.io/tanisheesh/reqly-dashboard:0.15.0
    ```

=== "Static host"

    ```bash
    cd dashboard
    VITE_COLLECTOR_URL=https://reqly.example.com VITE_READ_KEY=... npm run build
    ```

    Deploy `dist/` to any static host: Render, Vercel, Netlify, S3 + CloudFront, or nginx.

Add the dashboard's URL to the collector's `CORS_ORIGINS`.

## Verify

- [ ] `GET /v1/ready` returns `{"status": "ready", "database": "ok"}`
- [ ] Send traffic from an instrumented app; the service appears on the dashboard within a minute or two
- [ ] With an LLM configured, Ask Reqly answers a question
- [ ] A test alert reaches your channels (Slack, Discord, Teams, PagerDuty, Opsgenie, email or webhook)

## Upgrade and roll back

- **Upgrade:** run the new collector image. Pending migrations apply on start-up. Read the [changelog](https://github.com/tanisheesh/reqly/blob/main/CHANGELOG.md) first: 0.14.0 changed the defaults outside demo mode (keys required, sign-in, no CORS).
- **Roll back:** run the previous image tag. Migrations are forward-only, so an older collector keeps working on a newer schema only when the change was additive. Back up the database before upgrading across several versions.

## Limits to know

- **One collector instance:** rate limits, the Ask Reqly budget and caches are per process. (Scheduled checks are claimed in the database, so a second instance during a rolling deploy doesn't repeat them.)
- **Freshness:** the most recent ~1 minute of data isn't in the aggregates yet.
- **Public dashboard:** with `PUBLIC_DASHBOARD` on, anyone with the dashboard URL can read every project. It's meant for demos.
