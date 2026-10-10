---
title: OpenAPI drift
description: Compare your OpenAPI spec with the API that is actually called.
---

# OpenAPI drift

Upload a service's OpenAPI spec and Reqly compares it with the last 30 days of real traffic. The dashboard's **API surface** panel then lists:

- **Undocumented:** operations that get traffic but aren't in the spec
- **Unused:** operations in the spec that nobody called
- **Deprecated but called:** operations marked `deprecated` that still get traffic, and with [consumer tracking](consumers.md) on, which clients still call them

It also shows the spec's coverage and the share of traffic that goes to undocumented endpoints.

## Upload a spec

=== "From the app (FastAPI, Litestar)"

    ```python
    reqly.instrument(app, push_openapi=True)   # or REQLY_PUSH_OPENAPI=true
    ```

    The SDK uploads the app's own spec on the first request.

=== "From CI (any app)"

    ```bash
    curl -X PUT http://localhost:8000/v1/services/checkout-api/openapi \
      -H "X-Reqly-Key: demo-key" -H "Content-Type: application/yaml" \
      --data-binary @openapi.yaml
    ```

    OpenAPI 3 or Swagger 2, JSON or YAML. Add `?base_path=/api` if the app serves the spec's paths under a prefix; Swagger 2's `basePath` is used automatically.

There is one spec per service; uploading again replaces it. `DELETE /v1/services/{service}/openapi` removes it. Uploading needs the ingest key, an admin session, or a project key with the `admin` scope.

## How paths are matched

Paths are compared by shape, so every framework's parameter syntax lines up: `{user_id}`, `{id}`, `:id` and `<int:id>` are all the same parameter. An un-templated route such as `/users/42` (from an OpenTelemetry exporter without `http.route`) still matches `/users/{id}`. A literal segment beats a parameter, so `/users/me` matches `/users/me`, not `/users/{id}`.

HEAD and OPTIONS requests, and `__unmatched__` 404s, are left out of the undocumented list.

[Ask Reqly](ask-reqly.md) can query the drift report too (`get_api_drift`), and the [weekly report](alerts.md#weekly-report) sums it up in one line.
