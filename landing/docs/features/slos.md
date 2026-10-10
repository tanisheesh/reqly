---
title: SLOs & error budgets
description: Availability and latency objectives with error budgets and multi-window burn-rate alerts.
---

# SLOs & error budgets

An SLO is one objective for a service, or for one route, over a rolling window:

- **availability:** the share of requests without an error is at least the target
- **latency:** the share of requests at or under a threshold is at least the target

For each SLO the dashboard shows the current **SLI**, the **error budget left**, and **burn rates** over 5 minutes, 30 minutes, 1 hour and 6 hours.

## Define an SLO

SLOs are managed with the ingest key, an admin's session, or a project key with the `admin` scope. The read key can't change anything.

```bash
# 99.5% of /checkout requests succeed, over 28 days
curl -X PUT http://localhost:8000/v1/slos \
  -H "X-Reqly-Key: demo-key" -H "Content-Type: application/json" \
  -d '{"service_name": "checkout-api", "name": "checkout availability",
       "route": "/checkout", "objective": "availability", "target": 0.995}'

# 95% of all requests take 800 ms or less
curl -X PUT http://localhost:8000/v1/slos \
  -H "X-Reqly-Key: demo-key" -H "Content-Type: application/json" \
  -d '{"service_name": "checkout-api", "name": "API latency",
       "objective": "latency", "target": 0.95, "latency_threshold_ms": 800}'
```

| Field | Required | Notes |
|---|---|---|
| `service_name` | ✅ | |
| `name` | ✅ | SLOs are created or updated by service + name |
| `objective` | ✅ | `availability` or `latency` |
| `target` | ✅ | e.g. `0.995` |
| `latency_threshold_ms` | for latency | |
| `route` | | Leave it out for a service-wide SLO |
| `window_days` | | 1–90, default 28 |

`GET /v1/slos` lists SLOs with their live status; `DELETE /v1/slos/{id}` removes one. The local demo creates three SLOs for the demo services on start-up.

## Burn-rate alerts

Every 5 minutes, Reqly applies the multi-window burn-rate rules from the Google SRE workbook:

| Alert | Fires when |
|---|---|
| **Fast burn** | the 1-hour **and** 5-minute burn rates are both ≥ 14.4 |
| **Slow burn** | the 6-hour **and** 30-minute burn rates are both ≥ 6 |

The longer window also needs at least 20 requests. The short window makes the alert stop soon after the problem does.

There is one open alert per SLO. It goes to the same channels as [anomaly alerts](alerts.md#channels) and resolves after 30 minutes out of burn.

## How it's computed

- **SLI and error budget** come from the per-minute aggregates, so a 28-day window is cheap. Latency SLOs read the share of requests under the threshold straight from the merged latency sketch.
- **Burn rates** come from raw events, which are current to the second.
