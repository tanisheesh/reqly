# Engineering Decisions — Reqly

<!--
This is not user documentation. This is for technical interviewers
and senior engineers who want to understand WHY the system is built
the way it is. Every entry answers a question an interviewer might ask.
-->

---

## Decision 1 — Z-score anomaly detection before the LLM, not LLM-first

**Context:** The AI insights feature needs to surface which routes degraded and when. The obvious approach is to send raw or aggregated metrics to the LLM and ask it to find anomalies. The alternative is to run statistical detection first and only send confirmed findings to the LLM.

**Decision:** Z-score over a day-of-week × hour-of-day seasonal baseline runs first. The LLM receives only the pre-filtered anomaly list — never raw metrics.

**Reason:** Sending raw metrics to an LLM and asking it to spot problems means the model can hallucinate patterns (a 2% error rate on Monday looks "notable" without a baseline). It also costs money on every weekly run even when nothing is wrong — the LLM call only happens when there are actual deviations to explain. Z-score is deterministic and auditable: every finding in the report can be traced back to a specific `(route, day_of_week, hour)` cell with a numeric z-score. That's defensible in an incident review in a way that "the LLM said so" is not.

**Tradeoff:** Z-score over a seasonal grid requires at least 3 baseline samples per `(route, dow, hour)` cell to avoid false positives on thin data. New routes or routes with low traffic in specific time slots produce no anomalies even if something changes. This is by design — insufficient data produces no signal rather than a false alarm — but it means the tool is less useful in the first 3–4 weeks of deployment.

---

## Decision 2 — Fail-open SDK design (self-disable rather than raise)

**Context:** The SDK runs inside the instrumented application's process. Any exception that escapes the SDK becomes an unhandled error in the application's request path, which means Reqly could cause production incidents in the apps it's supposed to observe.

**Decision:** Every public SDK method (`record_request`, `shutdown`, buffer operations, shipper calls) is wrapped so that on the first internal failure, the SDK logs once at WARNING level and sets `_disabled = True`. All subsequent calls are no-ops.

**Reason:** The SDK is instrumentation — it has no business crashing the host. A metric not captured is an acceptable loss. A 500 returned to a user because Reqly's background thread hit an unexpected state is not. The daemon thread design compounds this: it dies with the main process (no orphan threads), and all outbound HTTP calls carry explicit per-phase timeouts (connect 1 s, read 2 s, write 2 s) — a slow or unreachable collector cannot make the host application wait.

**Tradeoff:** Self-disabling means a misconfiguration (wrong collector URL, wrong API key) will silently produce no data after the first WARNING log. This is harder to debug than an exception at startup. The `reqly.stats()` function exposes the disabled flag and event counters so developers can check instrumentation health — but only if they know to look.

---

## Decision 3 — TimescaleDB over regular Postgres

**Context:** Reqly stores one row per HTTP request. At any real traffic volume that's millions of rows quickly. Every dashboard query is a time-range aggregation (p95 latency over the last 6 hours, grouped by route) — the exact worst case for a regular Postgres table without very careful indexing.

**Decision:** TimescaleDB hypertable for `request_events` (1-day chunks), with continuous aggregates pre-computing per-minute and per-hour rollups.

**Reason:** Hypertables partition the data into time-based chunks automatically — a 6-hour query touches only the last 6 chunks rather than scanning the full table. Continuous aggregates run the heavy `percentile_cont()` aggregation on insert rather than on every dashboard load, so dashboard queries hit a pre-computed materialized view instead of the raw event table. The wire protocol and query language are identical to standard Postgres — no new ORM, no migration friction, and asyncpg works unchanged.

**Tradeoff:** TimescaleDB requires the TimescaleDB extension, which means the standard Postgres Docker image isn't enough. This adds a small operational dependency — self-hosters need to use the `timescale/timescaledb` image. Managed Postgres offerings (Neon, Supabase, RDS) don't include TimescaleDB, so production deployment requires either EC2 or a TimescaleDB Cloud account. This is documented and mitigated by the provided EC2 user-data script that auto-installs everything.

---

## Decision 4 — Bounded cardinality: route templates over raw paths

**Context:** The SDK captures the route for each request. The naive approach is to capture the raw URL path (`/users/12345`). The alternative is to capture the framework's matched route template (`/users/{id}`).

**Decision:** Use the framework's matched route template. Unmatched paths (404s, probes) collapse to a single `__unmatched__` bucket.

**Reason:** If Reqly stored raw paths, `/users/1` through `/users/1000000` each become a distinct metric label. A service with a million users generates a million distinct "routes" — storage explodes, every aggregate query becomes meaningless noise, and the dashboard's "top routes" table becomes useless. Cardinality stays O(number of routes defined in the app), not O(unique URLs ever requested), regardless of traffic volume. Both FastAPI and Flask expose the matched template after routing, so this is free — the SDK reads `scope["route"].path` (FastAPI) or `request.url_rule.rule` (Flask) in the response finalizer.

**Tradeoff:** Route templates require the framework to have successfully matched the request. 404s and unrecognized paths get no template — they collapse into `__unmatched__`, which means you can count unmatched traffic but can't distinguish `/old-endpoint` from `/robots.txt` in the metrics. For v1 this is acceptable; a future improvement could use a per-service allowlist of "known unmatched" routes.

---

## Decision 5 — Pure ASGI middleware over Starlette's BaseHTTPMiddleware

**Context:** FastAPI is a Starlette app. Starlette's `BaseHTTPMiddleware` is the documented way to add middleware, but it has a known limitation: it buffers streaming response bodies before passing them to the next middleware.

**Decision:** Implement `ReqlyASGIMiddleware` as a raw ASGI callable that wraps `send` rather than subclassing `BaseHTTPMiddleware`.

**Reason:** `BaseHTTPMiddleware` buffers the entire response body to enable before/after access. For apps that stream large responses (file downloads, server-sent events, chunked JSON), this turns a streaming response into an in-memory buffer, doubling memory use and breaking streaming semantics. The raw ASGI approach wraps only the `send` callable to intercept the `http.response.start` message (which carries the status code) — the response body is never touched. Duration is measured from before calling the inner app to after the `finally` block, which fires when the response is fully sent.

**Tradeoff:** Raw ASGI middleware is lower-level than `BaseHTTPMiddleware` — error propagation and edge cases (WebSocket upgrades, HTTP/2 push) need to be handled explicitly. The current implementation handles `scope["type"] != "http"` by passing through, which covers the common cases. WebSocket metrics are not captured in v1.

---

## What I'd do differently in v2

- **Configurable anomaly threshold per service** — the global z > 2.0 threshold works for balanced traffic but produces too many false positives for high-volume services and too few anomalies for low-volume ones. Per-service thresholds or adaptive thresholds based on historical false-positive rates would improve signal quality.
- **Structured logging from the SDK** — currently the SDK uses Python's stdlib `logging` at WARNING level. Structured JSON logs (with service name, event counts, error types) would make SDK health far easier to observe in a log aggregator.
- **Collector connection pooling configuration exposed** — the asyncpg pool min/max sizes are env vars but not documented in the SDK README. Under high ingest volume, pool exhaustion is a silent failure; exposing this more clearly would help operators.
- **End-to-end integration test against real TimescaleDB** — current tests mock the DB. A Docker-based integration test suite that spins up a real TimescaleDB container would catch schema drift and continuous aggregate policy bugs before deploy.

---

## Explicit non-decisions (deferred to v2)

| Feature | Why deferred |
|---|---|
| OpenTelemetry OTLP export | Would require stabilizing the collector wire protocol as a public spec; the proprietary format is simpler for v1 self-hosting |
| Alerting webhooks | Requires false-positive tuning (a threshold that fires too often trains users to ignore it); not enough historical data from v1 deployments to calibrate |
| Non-Python SDKs | Node.js/Go SDKs require the same fail-open, bounded-cardinality guarantees reimplemented in a different runtime; better to prove the protocol with Python first |
| Real-time streaming (WebSockets/SSE) | 30-second poll is sufficient for the observability use case; persistent connections add complexity to the collector and ops burden |
| Multi-tenant key management | Single-tenant self-hosting covers the v1 use case; per-customer isolation requires a billing/auth layer that is out of scope |
