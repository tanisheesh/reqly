---
title: CLI & GitHub Action
description: Deploy markers and OpenAPI uploads from CI with the reqly command or the Reqly GitHub Action.
---

# CLI & GitHub Action

The SDKs learn a release from the first request it serves. A **deploy marker** records it when it ships instead, so the release marker on the charts, the releases table, alert release context and Ask Reqly put it where it really started, even for a quiet service. The same tools upload the OpenAPI spec from CI.

## GitHub Action

```yaml
- uses: tanisheesh/reqly@v1
  with:
    collector-url: https://reqly.example.com
    api-key: ${{ secrets.REQLY_INGEST_KEY }}
    service: checkout-api
    environment: production          # optional
    openapi-spec: openapi.yaml       # optional: upload it too
```

| Input | Default | |
|---|---|---|
| `collector-url` | required | |
| `api-key` | required | an ingest key, or a project key with the ingest scope. From a secret |
| `service` | required | the service name the SDK reports |
| `release` | `${{ github.sha }}` | |
| `environment` | empty | |
| `openapi-spec` | empty | path to a JSON or YAML spec; empty skips the upload |
| `base-path` | empty | prefix for the spec's paths, e.g. `/api` |
| `deploy-marker` | `true` | `false` to only upload the spec |

It only needs `curl` and `bash`, which every GitHub runner has. A refused request fails the step with the collector's reply.

## `reqly` CLI

Comes with the Python SDK (0.8+):

```bash
pipx install reqly        # or: pip install reqly

export REQLY_COLLECTOR_URL=https://reqly.example.com REQLY_API_KEY=...

reqly deploy --service checkout-api --environment production    # release from GITHUB_SHA, RENDER_GIT_COMMIT, ...
reqly deploy --service checkout-api --release v42 --at 2026-10-23T09:00:00Z
reqly openapi push --service checkout-api openapi.yaml --base-path /api
reqly ping                                                      # collector up? key accepted?
```

The release comes from `--release`, `REQLY_RELEASE`, or the CI and hosting variables the SDKs read. `--collector-url` / `--api-key` override the environment. Every command exits non-zero on a failure.

## The API behind them

```bash
curl -X POST https://reqly.example.com/v1/deployments \
  -H "X-Reqly-Key: $REQLY_API_KEY" -H "Content-Type: application/json" \
  -d '{"service_name": "checkout-api", "release": "v42", "environment": "production"}'
```

`deployed_at` (ISO 8601) is optional and defaults to now; it may be up to 400 days back. Sending a marker again is harmless: the earliest time a release was seen, deployed or served, is kept. See the [HTTP API](http-api.md).
