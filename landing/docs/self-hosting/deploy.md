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
  ghcr.io/tanisheesh/reqly-collector:0.10.0
```

Images are built for linux/amd64 and arm64. Pin a version rather than `latest`.

Also set:

- **Behind a platform proxy** (Render, Fly.io, a load balancer): `FORWARDED_ALLOW_IPS=*`, so rate limits see real client IPs. Use `*` only if the collector is reachable solely through that proxy.
- `GROQ_API_KEY` for AI features, and alert webhooks.
- `PUBLIC_DASHBOARD=false` and `REQLY_ADMIN_PASSWORD` unless the dashboard should be public. See [Projects, keys & sign-in](access.md).

Check it with `GET /v1/health`, which returns `{"status": "ok"}`.

!!! tip "Render or similar"
    Create a Web Service from the repository with root directory `collector` and the Docker runtime, or deploy the prebuilt image. Add the variables above.

## 3. Dashboard

=== "Prebuilt image"

    Configured at start-up, so one image works with any collector:

    ```bash
    docker run -d -p 5173:5173 \
      -e REQLY_COLLECTOR_URL=https://reqly.example.com \
      -e REQLY_READ_KEY=... \
      ghcr.io/tanisheesh/reqly-dashboard:0.10.0
    ```

=== "Static host"

    ```bash
    cd dashboard
    VITE_COLLECTOR_URL=https://reqly.example.com VITE_READ_KEY=... npm run build
    ```

    Deploy `dist/` to any static host: Render, Vercel, Netlify, S3 + CloudFront, or nginx.

Add the dashboard's URL to the collector's `CORS_ORIGINS`.

## Optional: weekly report on AWS Lambda

The collector runs the weekly report itself. To run it as a Lambda instead (EventBridge schedule, S3 archive), deploy [`infra/sam`](https://github.com/tanisheesh/reqly/tree/main/infra/sam) with `sam build && sam deploy --guided`, then set `INSIGHTS_SCHEDULER_ENABLED=false` on the collector.

## Verify

- [ ] `GET /v1/health` returns 200
- [ ] Send traffic from an instrumented app; the service appears on the dashboard within a minute or two
- [ ] With `GROQ_API_KEY` set, Ask Reqly answers a question
- [ ] A test alert reaches your Slack, Discord or webhook

## Upgrade and roll back

- **Upgrade:** run the new collector image. Pending migrations apply on start-up.
- **Roll back:** run the previous image tag. Migrations are forward-only, so an older collector keeps working on a newer schema only when the change was additive. Back up the database before upgrading across several versions.

## Limits to know

- **One collector instance:** the scheduler, rate limits and caches are per process.
- **Freshness:** the most recent ~1 minute of data isn't in the aggregates yet.
- **Public dashboard:** with `PUBLIC_DASHBOARD` on, anyone with the dashboard URL can read every project. It's meant for demos.
