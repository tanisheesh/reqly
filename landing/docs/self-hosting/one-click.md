---
title: One-click deploy
description: Deploy Reqly on Render, Coolify or Railway.
---

# One-click deploy

Each of these runs the [all-in-one image](deploy.md#all-in-one-two-containers) (the collector serving the dashboard) next to TimescaleDB, with generated keys and an admin password. Sign in as `admin` with that password, then [instrument an app](../quickstart.md#2-instrument-your-app) with the generated ingest key.

## Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/tanisheesh/reqly)

The Blueprint ([`render.yaml`](https://github.com/tanisheesh/reqly/blob/main/render.yaml)) creates two services: `reqly` (web) and `reqly-db` (TimescaleDB as a private service with a 10 GB disk). Render generates `REQLY_INGEST_KEY`, `REQLY_READ_KEY`, `REQLY_ADMIN_PASSWORD` and the database password; find them in each service's **Environment** tab. Both need paid instances, since a private service with a disk isn't on Render's free plan. Fill in `GROQ_API_KEY` and alert webhooks when asked, or later.

## Coolify

**New resource → Docker Compose**, and use [`deploy/coolify/docker-compose.yml`](https://github.com/tanisheesh/reqly/blob/main/deploy/coolify/docker-compose.yml) (paste it, or point Coolify at the repository with that path). Coolify gives the app a domain and generates `SERVICE_PASSWORD_POSTGRES`, `SERVICE_PASSWORD_64_INGEST` (the ingest key), `SERVICE_PASSWORD_64_READ` and `SERVICE_PASSWORD_ADMIN` (the admin password), shown under the resource's environment variables.

## Railway

1. **New project → Deploy a template → TimescaleDB** (or any TimescaleDB with the Toolkit, e.g. the `timescale/timescaledb-ha:pg16` image with a volume on `/home/postgres/pgdata`).
2. **New → GitHub repo → `tanisheesh/reqly`**: [`railway.json`](https://github.com/tanisheesh/reqly/blob/main/railway.json) builds the all-in-one image and health-checks `/v1/ready`. Or **New → Docker image → `ghcr.io/tanisheesh/reqly`**.
3. On that service set `DATABASE_URL` to the database's (Railway's variable reference, e.g. `${{TimescaleDB.DATABASE_URL}}`), `REQLY_INGEST_KEY`, `REQLY_READ_KEY` and `REQLY_ADMIN_PASSWORD` (`${{secret(48)}}` generates one), and generate a domain.

To share it as a button, publish the project as a template from Railway's dashboard; Railway gives you its deploy URL.

## Without a template

The collector also takes the database as `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` and `PGDATABASE` (and `PGSSLMODE`) when `DATABASE_URL` isn't set, which is what most platforms hand out. Everything else is in [Configuration](configuration.md).
