# Ingest API spec (v2)

The contract every Reqly SDK implements. Write SDKs for other languages against
this document, not against the Python SDK's code.

Non-Python apps can also skip writing an SDK entirely and send OpenTelemetry traces
instead (planned: `POST /otlp/v1/traces`).

---

## Endpoint

```
POST /v1/ingest
Content-Type: application/json
X-Reqly-Key: <ingest key>
```

| Response | Meaning | SDK should |
|---|---|---|
| `200 {"accepted": n, "rejected": m}` | Batch processed. `rejected` events failed validation and were dropped individually. | Treat as success. |
| `401` | Missing or wrong ingest key. | Drop the batch, don't retry. |
| `422` | Batch envelope invalid (e.g. `service_name` missing or too long, more than 1,000 events). | Drop the batch, don't retry. |
| `408`, `429`, any `5xx` | Transient (rate limited, collector restarting, proxy error). | Retry with exponential backoff. |

Retries are safe: events are deduplicated on `(timestamp, event_id)`, so resending a
batch that actually landed never double-counts.

---

## Batch envelope

```json
{
  "service_name": "checkout-api",
  "sdk_version": "0.2.0",
  "release": "a1b2c3d",
  "environment": "prod",
  "events": [ ... ]
}
```

| Field | Type | Required | Limits | Notes |
|---|---|---|---|---|
| `service_name` | string | yes | 1–128 chars | One service per batch. |
| `sdk_version` | string | no | — | Informational. |
| `release` | string | no | ≤ 128 chars | **v2.** Default for events that don't set their own. Git SHA or version. |
| `environment` | string | no | ≤ 32 chars | **v2.** Default for events that don't set their own (`prod`, `staging`, …). |
| `events` | array | yes | 1–1,000 | Each event is validated independently. |

Unknown top-level fields are ignored, so newer SDKs can talk to older collectors.

---

## Event

```json
{
  "event_id": "4f1c2b0e-8d7a-4c1e-9b3f-2a6d5e7c8b90",
  "timestamp": "2026-10-09T14:03:22.418Z",
  "method": "GET",
  "route": "/orders/{id}",
  "status_code": 200,
  "duration_ms": 42.7,
  "error": false,
  "error_type": null,
  "host": "web-7f9c",
  "request_bytes": 0,
  "response_bytes": 1832
}
```

| Field | Type | Required | Limits | Notes |
|---|---|---|---|---|
| `event_id` | UUID string | yes | — | Unique per request. Dedup key — generate once, reuse on retry. |
| `timestamp` | ISO-8601 with timezone | yes | — | When the request completed. Naive timestamps are rejected. |
| `method` | string | yes | 1–16 chars | HTTP method, upper case. |
| `route` | string | yes | 1–512 chars | **The framework's route template** (`/orders/{id}`), never the raw path (`/orders/123`). Send `__unmatched__` when no route matched (404s, scanners). |
| `status_code` | int | yes | 100–599 | Response status sent to the client. |
| `duration_ms` | number | yes | 0–300,000 | Wall time from request start to response complete. |
| `error` | bool | yes | — | `true` for unhandled exceptions and any `5xx`. |
| `error_type` | string | no | ≤ 255 chars | Exception class name, e.g. `TimeoutError`. |
| `host` | string | no | ≤ 255 chars | Hostname / pod name. Used for "errors concentrated on one host" hints. |
| `release` | string | no | ≤ 128 chars | **v2.** Overrides the batch-level value. |
| `environment` | string | no | ≤ 32 chars | **v2.** Overrides the batch-level value. |
| `consumer_id` | string | no | ≤ 128 chars | **v2.** Identifies the API client. **Hash it in the SDK** (e.g. HMAC-SHA256 with a per-app salt) — never send raw API keys or user ids. |
| `request_bytes` | int | no | 0–10 GiB | **v2.** Request body size. Omit or send `null` when unknown. |
| `response_bytes` | int | no | 0–10 GiB | **v2.** Response body size. Omit or send `null` when unknown. |
| `llm_model` | string | no | ≤ 255 chars | **v2.** Model used while serving this request, if any. |
| `llm_input_tokens` | int | no | 0–10,000,000 | **v2.** |
| `llm_output_tokens` | int | no | 0–10,000,000 | **v2.** |

Any other fields on an event are ignored.

---

## SDK behaviour requirements

These are what make Reqly safe to run inside someone else's app. A new SDK should
meet all of them.

1. **Fail open.** No SDK error may propagate into the host application. On an
   internal failure, log once and disable instrumentation.
2. **Never block the request path.** Record into an in-memory queue; ship from a
   background worker. HTTP calls use short connect/read timeouts.
3. **Bounded memory.** Fixed-size queue; drop the *oldest* events when full and count
   the drops.
4. **Bounded cardinality.** Route templates only (see `route` above).
5. **Batching.** Up to 1,000 events per request; flush on an interval (default 5s) or
   when a batch fills.
6. **Retry policy.** As in the response table above; exponential backoff with jitter,
   a small fixed number of attempts, then drop and count.
7. **Release detection.** `release` from explicit config, then `REQLY_RELEASE`, then
   common CI/hosting variables (`GIT_COMMIT`, `GITHUB_SHA`, `CI_COMMIT_SHA`,
   `RENDER_GIT_COMMIT`, `VERCEL_GIT_COMMIT_SHA`, …).
8. **Fork safety** where the runtime forks workers after import (e.g. Python's
   gunicorn `--preload`): restart the background worker in the child.

---

## Changelog

- **v2** (collector 0.3.0): `release`, `environment` (batch and event level),
  `consumer_id`, `request_bytes`, `response_bytes`, `llm_*`. All optional; v1 payloads
  are unchanged and still accepted.
- **v1** (collector 0.1.0): initial contract.
