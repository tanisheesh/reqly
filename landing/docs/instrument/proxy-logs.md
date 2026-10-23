---
title: Proxy access logs
description: Send Caddy, Traefik, Nginx or Envoy access logs to Reqly, without touching the app.
---

# Proxy access logs

Can't change the app, or it's in a language without an SDK? Send the access logs of the reverse proxy in front of it. Reqly reads JSON access log lines and turns each into a request event.

```
POST /v1/ingest/logs?service_name=checkout-api&release=v42&environment=production
X-Reqly-Key: <ingest key>
Content-Type: application/x-ndjson     (one JSON object per line, or a JSON array)
Content-Encoding: gzip                 (optional)
```

Each line is used or rejected on its own; the response is `{"accepted", "rejected", "reasons"}` like `/v1/ingest`. Lines are deduplicated, so a shipper re-sending a batch adds nothing. At most 5,000 lines per request (collector 0.15+).

## Routes, not raw paths

A proxy only knows the raw path, `/users/42?tab=1`. Reqly never stores one: the query string is dropped, and the path becomes a **route template**:

1. With an [OpenAPI spec uploaded](../features/openapi-drift.md) for the service, the spec's path: `/users/42` → `/users/{user_id}`, `/users/me` → `/users/me`.
2. Without one, id-like segments are collapsed: numbers, UUIDs, long hex and token-like strings become `{id}` (`/orders/9f8e…/items` → `/orders/{id}/items`). Slugs (`/blog/hello-world`) stay as they are, so **upload a spec** for APIs with slug or name segments.

Errors are 5xx responses, the same as the SDKs. Trace ids are kept for 4xx/5xx and slow requests when the proxy logs the `traceparent` header.

## Caddy

Caddy's JSON access log is read as it is.

```caddyfile
api.example.com {
    log {
        output file /var/log/caddy/access.log
        format json
    }
    reverse_proxy app:8000
}
```

## Traefik

Traefik's JSON access log is read as it is. Keep the `traceparent` header if you want trace ids:

```yaml
accessLog:
  format: json
  fields:
    headers:
      names:
        Traceparent: keep
```

## Nginx

Nginx needs a JSON `log_format` with the request time:

```nginx
log_format reqly escape=json '{"time":"$time_iso8601","method":"$request_method",'
  '"request_uri":"$request_uri","status":$status,"request_time":$request_time,'
  '"body_bytes_sent":$body_bytes_sent,"request_length":$request_length,'
  '"host":"$host","traceparent":"$http_traceparent"}';
access_log /var/log/nginx/reqly.log reqly;
```

The default `combined` format has no request time, so its lines are rejected (`no duration`).

## Envoy, HAProxy and others

Any JSON line with these fields works:

| Field | Required | |
|---|---|---|
| `method` | ✅ | |
| `path` (or `uri`, `request_uri`) | ✅ | the query string is dropped |
| `status` (or `response_code`) | ✅ | |
| `duration_ms`, or `request_time` in seconds, or `duration` in ms | ✅ | |
| `time` (or `timestamp`, `start_time`) | | ISO 8601 or epoch seconds; default: when the line arrives |
| `host`, `bytes_sent`, `bytes_received`, `traceparent` | | |

For Envoy, a `json_format` access log with `"method": "%REQ(:METHOD)%"`, `"path": "%REQ(X-ENVOY-ORIGINAL-PATH?:PATH)%"`, `"response_code": "%RESPONSE_CODE%"`, `"duration": "%DURATION%"` and `"start_time": "%START_TIME%"`.

## Shipping the lines

[Vector](https://vector.dev) tails the file and posts batches:

```toml
[sources.access]
type = "file"
include = ["/var/log/caddy/access.log"]

[transforms.parse]
type = "remap"
inputs = ["access"]
source = ". = parse_json!(.message)"

[sinks.reqly]
type = "http"
inputs = ["parse"]
uri = "https://reqly.example.com/v1/ingest/logs?service_name=checkout-api&environment=production"
method = "post"
encoding.codec = "json"
framing.method = "newline_delimited"
compression = "gzip"
batch.max_events = 1000
request.headers.X-Reqly-Key = "${REQLY_API_KEY}"
```

Fluent Bit's `http` output (`format json_lines`) or a cron job with `curl --data-binary @access.log` work the same way.
