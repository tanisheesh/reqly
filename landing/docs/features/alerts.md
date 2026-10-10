---
title: Alerts & weekly report
description: Hourly anomaly alerts with release context and root-cause hints, and a weekly AI-written report.
---

# Alerts & weekly report

Reqly looks for anomalies in two places. **Hourly alerts** tell you about a problem while it's happening. **The weekly report** sums up the week. Both use the same statistics; an LLM only writes the weekly narrative, never the detection.

## Hourly alerts

At :15 past every hour, the collector checks the last complete hour of every route. Each route is compared with **the same weekday and hour over the previous 8 weeks**, so Monday 9 am is compared with earlier Monday 9 ams, not with Sunday night.

- **Error rate** is tested with an exact Poisson tail on the real request and error counts, so a single failure on a quiet route doesn't look like a 33% error rate.
- **p95 latency** is only tested on hours with at least 100 requests.
- A finding needs z ≥ 4 **and** a material change: at least 2 percentage points more errors, or at least 50% higher p95.

Each alert includes:

- the observed values against the usual ones
- **release context:** the release that was running and, for a new one, before/after numbers against the previous release
- **root-cause hints**
- **affected consumers**, if [consumer tracking](consumers.md) is on

An alert from the demo data:

```
🔴 Anomaly — flask-demo /orders (Friday 15:00-16:00 UTC, z=5.37)
• error rate 30.0% vs 2.2% usual · p95 6588ms vs 1576ms usual
• running release v2 — vs v1: errors 2.6% → 33.1%, p95 2072ms → 4501ms
• 100% of errors came from host pod-3, which served 23% of requests
```

### Lifecycle

- **One open alert per route.** A new detection opens one and sends a notification.
- **Reminders:** while it keeps firing, a reminder goes out every `ALERT_RENOTIFY_HOURS` (default 6).
- **Resolved:** after 2 clean hours the alert resolves, with a final message.
- Open alerts are shown on the dashboard. `GET /v1/alerts?status=all` also returns recent resolved ones.

### Root-cause hints

Hints are computed from your data, not guessed by a model:

- **Concentration:** errors or slow requests concentrated on one **host** or **environment**, relative to its share of traffic. For example, *"92% of errors came from pod-7, which served 25% of requests"*.
- **Something new:** an **error type** or **status code** that dominates now and was rare the week before.

### Channels

Set any combination. Channels that aren't set are skipped.

| Variable | Sends to |
|---|---|
| `ALERT_SLACK_WEBHOOK_URL` | a Slack incoming webhook |
| `ALERT_DISCORD_WEBHOOK_URL` | a Discord webhook |
| `ALERT_WEBHOOK_URL` | any URL, as JSON: `{"event": "alert.opened" \| "alert.still_firing" \| "alert.resolved", "text": "...", "alert": {...}}` |
| `DASHBOARD_URL` | linked from Slack messages |

Set `ALERTS_ENABLED=false` to turn the hourly check off. [SLO burn-rate alerts](slos.md#burn-rate-alerts) go to the same channels.

!!! note "Run one collector"
    The scheduler runs inside the collector process. With two collector instances, every check runs twice.

## Weekly report

Every **Sunday at 23:00 UTC**, the collector compares the last 7 days with the 7 weeks before, using the same tests. It keeps the top 5 anomalies with their release context, hints and affected consumers.

With `GROQ_API_KEY` set, `openai/gpt-oss-120b` writes a short report from those findings. The model only sees the structured anomaly list, never raw events, and is told not to invent causes the data doesn't support. Without a key, or if the call fails, the findings are formatted as plain text.

The report shows on the dashboard. To generate it on demand:

```bash
curl -X POST "http://localhost:8000/v1/insights/generate?service_name=checkout-api" \
  -H "X-Reqly-Key: demo-read-key"
```

On-demand generation is limited to 5 per minute. A report generated in the last 10 minutes is returned as is, so the button can't run up LLM cost.

The weekly job can also run as an AWS Lambda ([`infra/sam`](https://github.com/tanisheesh/reqly/tree/main/infra/sam)). Set `INSIGHTS_SCHEDULER_ENABLED=false` on the collector then, so it doesn't run twice.
