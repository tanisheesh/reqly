---
title: HTTP API
description: Every endpoint the Reqly collector serves, with the access each one needs.
---

# HTTP API

The dashboard uses this API, and so can you. Every running collector also serves an interactive reference at **`/docs`** (Swagger UI), for example [localhost:8000/docs](http://localhost:8000/docs).

## Authentication

Send either:

- **a key** in the `X-Reqly-Key` header: the ingest key, the read key, or a project key (`rqk_…`)
- **a session** in `Authorization: Bearer <token>`, from `POST /v1/auth/login`

The **Access** column below means:

| Access | Who |
|---|---|
| **Read** | the read key (only while `PUBLIC_DASHBOARD=true`), a session, or a key with the `read` scope |
| **Ingest** | the ingest key, or a project key with the `ingest` scope |
| **Admin** | the ingest key, an admin session, or a project key with the `admin` scope |

Endpoints about one service check it against the caller's projects; list endpoints only return what the caller may see. See [Projects, keys & sign-in](../self-hosting/access.md).

## Ingest

| Method | Path | Access | Description |
|---|---|---|---|
| `POST` | `/v1/ingest` | Ingest | Batch of request events, ≤ 1,000 per call, validated one by one. [Spec](ingest-spec.md) |
| `POST` | `/otlp/v1/traces` | Ingest | OTLP/HTTP traces, protobuf or JSON, gzip. [OpenTelemetry](../instrument/opentelemetry.md) |

## Metrics

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/v1/services` | Read | Services with recorded traffic |
| `GET` | `/v1/services/{service}/routes` | Read | Route templates for a service |
| `GET` | `/v1/metrics/summary?service_name=&window=&route=` | Read | Latency and error-rate series, status codes, top routes, requests/min and release markers. `window`: `1h` (default), `6h`, `24h`, `7d` |
| `GET` | `/v1/services/{service}/releases` | Read | Recent releases: first/last seen, volume, error rate, p95 |

## Alerts, SLOs and reports

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/v1/alerts?service_name=&status=` | Read | Open alerts, or recent ones with `status=all`: hourly anomalies, SLO burn and LLM cost spikes |
| `GET` | `/v1/slos?service_name=` | Read | SLOs with SLI, budget left, burn rates and state |
| `PUT` | `/v1/slos` | Admin | Create or update an SLO. [Fields](../features/slos.md#define-an-slo) |
| `DELETE` | `/v1/slos/{id}` | Admin | Delete an SLO |
| `GET` | `/v1/insights/latest?service_name=` | Read | Latest weekly report |
| `POST` | `/v1/insights/generate?service_name=` | Read | Generate the report now. 5/min; cached for 10 minutes |

## API depth

| Method | Path | Access | Description |
|---|---|---|---|
| `PUT` | `/v1/services/{service}/openapi` | Admin | Upload an OpenAPI 3 / Swagger 2 spec, JSON or YAML. `?base_path=` prefixes its paths |
| `GET` | `/v1/services/{service}/openapi/drift` | Read | Undocumented, unused and deprecated-but-called operations |
| `DELETE` | `/v1/services/{service}/openapi` | Admin | Remove the spec |
| `GET` | `/v1/services/{service}/consumers?window=` | Read | Top consumers. `window`: `24h`, `7d` (default), `30d` |
| `GET` | `/v1/services/{service}/consumers/{id}?window=` | Read | One consumer's routes and daily requests |
| `GET` | `/v1/services/{service}/llm-usage?window=` | Read | LLM tokens and cost per route, model and day |
| `POST` | `/v1/ask` | Read | Ask Reqly: `{"service_name", "question"}`. 5/min per IP, `ASK_DAILY_LIMIT` per day. [Details](../features/ask-reqly.md) |

## Projects and keys

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/v1/projects` | Read | Projects the caller can see, with their services |
| `POST` | `/v1/projects` | Admin of every project | Create a project: `{"slug", "name"}` |
| `PUT` | `/v1/projects/{id}/services` | Admin of every project | Move a service, and its data, to the project |
| `GET` / `POST` | `/v1/projects/{id}/keys` | Project admin | List keys, or create one: `{"name", "scopes"}`. The key is returned once |
| `POST` | `/v1/keys/{id}/revoke` | Project admin | Revoke a key |
| `GET` / `POST` | `/v1/projects/{id}/members` | Admin of every project | List or add members |
| `DELETE` | `/v1/projects/{id}/members/{user_id}` | Admin of every project | Remove a member |

## Sign-in

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/v1/auth/config` | none | Whether the dashboard is public |
| `POST` | `/v1/auth/login` | none, 10/min per IP | Username and password → session token |
| `POST` | `/v1/auth/logout` | Session | End the session |
| `GET` | `/v1/auth/me` | Session | The signed-in user |
| `POST` | `/v1/auth/password` | Session | Change the password; ends all of the user's sessions |

## Health

| Method | Path | Access | Description |
|---|---|---|---|
| `GET` | `/v1/health` | none | Returns `{"status": "ok"}` |

## Limits

- Ingest: 600 requests/min per client IP.
- Request bodies over 16 MB are refused with 413, chunked bodies included.
- Errors are JSON: `{"detail": "..."}`. 401 means an unknown or missing key; 403 means a valid key without the right scope or project.
