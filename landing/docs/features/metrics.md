---
title: Metrics & releases
description: Latency percentiles, error rates, status codes and per-release health in the Reqly dashboard.
---

# Metrics & releases

The dashboard shows one service at a time, for the last **1h, 6h, 24h or 7d**, overall or for a single route:

- **Latency:** p50, p95 and p99 over time
- **Error rate** over time, and the status code distribution
- **Top routes** by traffic, with their error rate and p95
- **Requests per minute**
- **Release markers** on the charts, and a releases table with each release's first and last seen time, request volume, error rate and p95

Panels for [alerts](alerts.md), [SLOs](slos.md), [consumers](consumers.md), [LLM cost](llm-cost.md), [API surface](openapi-drift.md), [Ask Reqly](ask-reqly.md) and the weekly report appear once their data exists. The dashboard polls the collector every 30–60 seconds.

## Percentiles that are right

Percentiles can't be averaged. A p95 of per-minute p95s, or the max of per-route p95s, overstates latency, sometimes by a lot.

Reqly keeps a **mergeable latency sketch** (UddSketch, from the TimescaleDB Toolkit) per route and minute. Sketches merge exactly across routes and time, so a service's 7-day p95 is a real p95, within about 0.2% of the exact value.

## Errors

A request counts as an error when the status is 5xx or the app raised an unhandled exception. The SDK records the exception's class name as the **error type**, which feeds the [root-cause hints](alerts.md#root-cause-hints) and the breakdowns in [Ask Reqly](ask-reqly.md).

## Releases

Every event carries the release it was served by. The SDKs detect it from your CI or host (`GITHUB_SHA`, `RENDER_GIT_COMMIT`, `VERCEL_GIT_COMMIT_SHA`, …), and OpenTelemetry apps send it as `service.version`. Reqly uses it to:

- draw a marker on the charts when a new release appears
- show each release's error rate and p95 in the releases table
- tell you in every alert which release was running, and for a new release, how errors and p95 changed against the previous one

## Freshness and retention

| Data | Kept for | Notes |
|---|---|---|
| Raw request events | 14 days | Used where freshness or detail matters: breakdowns, burn rates, consumers up to 7 days |
| Per-minute aggregates | 90 days | Charts, percentiles, SLO windows, drift |
| Per-hour aggregates | 90–180 days | Anomaly baselines, consumers over 30 days, LLM usage |

New events show up in the aggregates within about a minute. Events that arrive late (an SDK retrying after an outage, or a backfill) are materialized by a background loop, so history fills in rather than staying empty.

Events older than 13 days, or more than 15 minutes in the future, are refused unless the batch is marked as a [backfill](../reference/ingest-spec.md#batch-envelope). This stops a single stray old event from rebuilding a whole hour of aggregate history from just itself.
