---
title: Kubernetes (Helm)
description: Install Reqly on Kubernetes with the Helm chart — collector, dashboard and TimescaleDB.
---

# Kubernetes (Helm)

The chart installs the collector, the dashboard and, unless you bring your own database, TimescaleDB (`timescale/timescaledb-ha:pg16`, with the Toolkit).

```bash
helm install reqly oci://ghcr.io/tanisheesh/charts/reqly --namespace reqly --create-namespace
```

The chart is published with every collector release, and its version is the collector's version. From a clone of the repository, use `helm install reqly deploy/helm/reqly` instead.

## Get the keys

The chart generates the ingest and read keys on first install and keeps them across upgrades:

```bash
kubectl -n reqly get secret reqly-secrets -o jsonpath='{.data.ingest-key}' | base64 -d; echo
kubectl -n reqly get secret reqly-secrets -o jsonpath='{.data.read-key}' | base64 -d; echo
```

Use the ingest key as the SDK's `api_key` or the OTLP `x-reqly-key` header. Apps inside the cluster reach the collector at `http://reqly-collector.reqly:8000`.

## Open the dashboard

Without an ingress, forward both services to your machine:

```bash
kubectl -n reqly port-forward svc/reqly-collector 8000:8000
kubectl -n reqly port-forward svc/reqly-dashboard 5173:5173
```

Then open [localhost:5173](http://localhost:5173). The dashboard runs in your browser and calls the collector directly, which is why the collector needs to be reachable from the browser too.

## With an ingress

The collector and the dashboard each get a host:

```yaml
# values.yaml
ingress:
  enabled: true
  className: nginx
  collector:
    host: reqly-collector.example.com
  dashboard:
    host: reqly.example.com
  tls:
    - secretName: reqly-tls
      hosts: [reqly.example.com, reqly-collector.example.com]

collector:
  config:
    CORS_ORIGINS: https://reqly.example.com
    DASHBOARD_URL: https://reqly.example.com
```

```bash
helm upgrade --install reqly oci://ghcr.io/tanisheesh/charts/reqly -n reqly -f values.yaml
```

The dashboard is pointed at `https://reqly-collector.example.com` automatically. Set `dashboard.collectorUrl` to override it.

## Sign-in, AI and alerts

```yaml
collector:
  config:
    PUBLIC_DASHBOARD: "false"
secrets:
  adminPassword: "at-least-12-characters"   # first admin, user "admin"
  groqApiKey: "gsk_..."                     # weekly AI report and Ask Reqly
  slackWebhookUrl: "https://hooks.slack.com/services/..."
```

Any other collector setting goes under `collector.config` (plain values) or `collector.extraEnv`. Every setting is listed in [Configuration](configuration.md).

To keep secrets out of values files, create a Secret yourself and pass `secrets.existingSecret`. It needs these keys: `ingest-key`, `read-key`, `database-url`, and optionally `postgres-password`, `groq-api-key`, `admin-password`, `slack-webhook-url`, `discord-webhook-url` and `webhook-url`.

## External database

```yaml
timescaledb:
  enabled: false
externalDatabaseUrl: postgresql://reqly:...@db.example.com:5432/reqly?sslmode=require
```

The database must be TimescaleDB with the Toolkit. The collector applies its migrations on start-up.

## What the chart sets up

| Resource | Notes |
|---|---|
| Collector Deployment + Service | Always **1 replica** with the `Recreate` strategy: the alert scheduler, rate limits and caches are per process, so two collectors would run every check twice. Probes on `/v1/health`; the startup probe allows a few minutes for migrations |
| Dashboard Deployment + Service | Static; scale with `dashboard.replicas` |
| TimescaleDB StatefulSet + Service | One instance with a `10Gi` PersistentVolumeClaim (`timescaledb.persistence`) |
| Secret | Keys, the database URL and optional secrets. Generated values survive `helm upgrade` |
| Ingress | Optional, one host per component |

Pods run as a non-root user (uid 1000). Pods restart when the chart's config or secret changes.

## Values

| Value | Default | Notes |
|---|---|---|
| `collector.image.tag` / `dashboard.image.tag` | the chart's `appVersion` | |
| `collector.config` | public dashboard, alerts on | Environment for the collector |
| `collector.resources` | 100m CPU, 256–512 Mi | |
| `dashboard.enabled` | `true` | |
| `dashboard.collectorUrl` | from the ingress, else `http://localhost:8000` | The URL the browser calls |
| `timescaledb.enabled` | `true` | `false` with `externalDatabaseUrl` |
| `timescaledb.persistence.size` | `10Gi` | Raw events are kept 14 days, aggregates 90–180 |
| `secrets.*` | generated / empty | See above |
| `ingress.*` | disabled | |

The full list with comments is in [`values.yaml`](https://github.com/tanisheesh/reqly/blob/main/deploy/helm/reqly/values.yaml).

## Upgrade and uninstall

```bash
helm upgrade reqly oci://ghcr.io/tanisheesh/charts/reqly -n reqly --reuse-values
helm uninstall reqly -n reqly   # the database PVC is kept; delete it to drop the data
```
