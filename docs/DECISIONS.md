# Engineering Decisions — Reqly

Why Reqly is built the way it is — the questions an interviewer or a reviewer would ask, with
the tradeoff each answer cost.

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

**Tradeoff:** Self-disabling means a misconfiguration (wrong collector URL, wrong API key) will silently produce no data after the first WARNING log. This is harder to debug than an exception at startup. The client returned by `reqly.instrument()` has `stats()` with the disabled flag and event counters, so developers can check instrumentation health — but only if they know to look.

---

## Decision 3 — TimescaleDB over regular Postgres

**Context:** Reqly stores one row per HTTP request. At any real traffic volume that's millions of rows quickly. Every dashboard query is a time-range aggregation (p95 latency over the last 6 hours, grouped by route) — the exact worst case for a regular Postgres table without very careful indexing.

**Decision:** TimescaleDB hypertable for `request_events` (1-day chunks), with continuous aggregates pre-computing per-minute and per-hour rollups.

**Reason:** Hypertables partition the data into time-based chunks automatically — a 6-hour query touches only the last 6 chunks rather than scanning the full table. Continuous aggregates run the heavy `percentile_cont()` aggregation on insert rather than on every dashboard load, so dashboard queries hit a pre-computed materialized view instead of the raw event table. The wire protocol and query language are identical to standard Postgres — no new ORM, no migration friction, and asyncpg works unchanged.

**Tradeoff:** TimescaleDB requires the TimescaleDB extension, which means the standard Postgres Docker image isn't enough. This adds a small operational dependency — self-hosters need to use the `timescale/timescaledb-ha` image (TimescaleDB plus the Toolkit, which provides the mergeable percentile sketches). Managed Postgres offerings (Neon, Supabase, RDS) don't include TimescaleDB, so production deployment requires either EC2 or a TimescaleDB Cloud account. This is documented and mitigated by the provided EC2 user-data script that auto-installs everything.

---

## Decision 4 — Bounded cardinality: route templates over raw paths

**Context:** The SDK captures the route for each request. The naive approach is to capture the raw URL path (`/users/12345`). The alternative is to capture the framework's matched route template (`/users/{id}`).

**Decision:** Use the framework's matched route template. Unmatched paths (404s, probes) collapse to a single `__unmatched__` bucket.

**Reason:** If Reqly stored raw paths, `/users/1` through `/users/1000000` each become a distinct metric label. A service with a million users generates a million distinct "routes" — storage explodes, every aggregate query becomes meaningless noise, and the dashboard's "top routes" table becomes useless. Cardinality stays O(number of routes defined in the app), not O(unique URLs ever requested), regardless of traffic volume. Every supported framework exposes the matched template after routing, so this is free — e.g. `scope["route"].path` (FastAPI), `request.url_rule.rule` (Flask), `resolver_match.route` (Django), `req.route.path` (Express), `routeOptions.url` (Fastify); generic WSGI/ASGI apps pass a `route_resolver`.

**Tradeoff:** Route templates require the framework to have successfully matched the request. 404s and unrecognized paths get no template — they collapse into `__unmatched__`, which means you can count unmatched traffic but can't distinguish `/old-endpoint` from `/robots.txt` in the metrics. For v1 this is acceptable; a future improvement could use a per-service allowlist of "known unmatched" routes.

---

## Decision 5 — Pure ASGI middleware over Starlette's BaseHTTPMiddleware

**Context:** FastAPI is a Starlette app. Starlette's `BaseHTTPMiddleware` is the documented way to add middleware, but it has a known limitation: it buffers streaming response bodies before passing them to the next middleware.

**Decision:** Implement `ReqlyASGIMiddleware` as a raw ASGI callable that wraps `send` rather than subclassing `BaseHTTPMiddleware`.

**Reason:** `BaseHTTPMiddleware` buffers the entire response body to enable before/after access. For apps that stream large responses (file downloads, server-sent events, chunked JSON), this turns a streaming response into an in-memory buffer, doubling memory use and breaking streaming semantics. The raw ASGI approach wraps only the `send` callable to intercept the `http.response.start` message (which carries the status code) — the response body is never touched. Duration is measured from before calling the inner app to after the `finally` block, which fires when the response is fully sent.

**Tradeoff:** Raw ASGI middleware is lower-level than `BaseHTTPMiddleware` — error propagation and edge cases (WebSocket upgrades, HTTP/2 push) need to be handled explicitly. The current implementation handles `scope["type"] != "http"` by passing through, which covers the common cases. WebSocket metrics are not captured in v1.

---

## Decision 6 — Mergeable percentile sketches over percentile_cont

**Context:** Continuous aggregates stored `percentile_cont` per route per minute. Percentiles can't be combined, so the service-level p95 was the max of the route p95s and a 7-day p95 the max of minute p95s — upper bounds that overstate latency, sometimes by an order of magnitude (a slow, rare route dominates).

**Decision:** Store a UddSketch per (minute, service, environment, route, method) via the TimescaleDB Toolkit and roll it up at query time.

**Reason:** Sketches merge exactly across routes and time, so every level gets a real percentile. `uddsketch(1000, 0.005)` measured within ~0.2% of exact p50/p95/p99 on log-normal latencies at ~130 bytes per route-minute; the Toolkit's default `percentile_agg` was ~2% off at p95.

**Tradeoff:** Needs the Toolkit (`timescaledb-ha` image or Timescale Cloud). The migration detects it and the collector falls back to the old views without it, so plain `timescale/timescaledb` installs keep working.

---

## Decision 7 — Ask Reqly uses fixed query tools, not text-to-SQL

**Context:** Natural-language questions need data the model can't see. Letting it write SQL is the most flexible option, but a read key that ships in the dashboard bundle would then be a SQL console, and generated SQL over percentile sketches and hypertables is easy to get subtly wrong (averaging percentiles, scanning 14 days of raw events).

**Decision:** Nine fixed tools with validated arguments over the same aggregates the dashboard uses, returning rounded numbers. The answer comes with the list of calls and their results, and every number in the answer is looked up in those results; numbers that aren't found are flagged on the dashboard.

**Reason:** The model chooses what to look at, never how to query it, so the worst case is a wrong question, not a wrong or expensive query. Verification catches the remaining failure mode: models occasionally mis-copy digits ("4 778" for 478).

**Tradeoff:** Questions outside the tools (anything not in Reqly's data, like sign-up counts) get "can't answer from the available data" until a tool exists for them. Each question costs a few LLM calls, so it is rate-limited per IP and capped per day.

---

## Decision 8 — Bearer session tokens, not cookies

**Context:** Dashboard sign-in needs a session. The dashboard and the collector usually run on different hosts — on the live demo two `*.onrender.com` services, which browsers treat as different sites because `onrender.com` is on the Public Suffix List.

**Decision:** A random 256-bit session token returned by `/v1/auth/login`, sent as `Authorization: Bearer`, stored by the dashboard in `localStorage`; the collector keeps only its SHA-256 with an expiry.

**Reason:** A cross-site cookie is a third-party cookie, which Safari and Firefox block by default — sign-in would work in one browser and silently fail in another. A bearer header also removes CSRF from the picture.

**Tradeoff:** A token in `localStorage` is readable by any script on the dashboard's origin, so the dashboard must stay free of injected HTML (React escapes everything; nothing uses `dangerouslySetInnerHTML`). Tokens expire after 7 days and a password change ends every session.

---

## Decision 9 — Projects own services instead of tagging every event

**Context:** Several teams sharing one collector need their own keys and must not see each other's data. The obvious design is a `project_id` column on `request_events` and on every continuous aggregate.

**Decision:** A `project_services` table maps each service to one project. Keys and members are per project; queries keep filtering by service, and the auth layer checks the service's project (from the path, query or body).

**Reason:** No change to the hypertable, the aggregates or any query; existing data moves to the `default` project in one migration, and moving a service between projects moves its whole history instantly. A new service joins the project of the key that sends it first, so teams don't have to register services ahead of time.

**Tradeoff:** A service name is unique across projects — two teams can't both have `api`. Lookups add a query per request, so keys and service ownership are cached for 60 s, which delays a revocation by up to a minute on other instances.

---

## Decision 10 — Refuse events older than the raw data, unless it's an explicit backfill

**Context:** Raw events are kept 14 days, aggregates 90–180. The late-data refresher rebuilt the aggregates from the oldest late event up to now. A single event older than 14 days (a skewed SDK clock, an exporter flushing late) made it rebuild days whose raw chunks were already gone — from that one event. A test reproduced an hour of 30 requests becoming 1.

**Decision:** Ingest and OTLP refuse events older than 13 days or more than 15 minutes in the future, with the reason in the response. A batch marked `"backfill": true` may import history; the refresher clamps everything else to 13 days.

**Reason:** History import is legitimate (the demo backfills 8 weeks), stragglers are not, and only the sender knows which one it is sending.

**Tradeoff:** A client whose clock is more than 15 minutes ahead loses events until it's fixed, and a backfill must send complete hours, because those hours are rebuilt from exactly what it sends.

---

## Decision 11 — Measure SDK overhead in CI instead of claiming it

**Context:** The README said "zero-overhead SDK". Nothing measured it.

**Decision:** Benchmarks for both SDKs (`bench/`) run the same app with and without Reqly, in-process where the framework allows, with consumer tracking on and the shipper sending throughout, in alternating rounds; a workflow reruns them on every SDK change.

**Reason:** The first run found a Node bug (a flush sent each event arriving during a send as its own batch: one collector request per app request under steady traffic) and Python work that could move off the request path. Overhead went from up to +330 µs to ~5–33 µs per request.

**Tradeoff:** Shared CI runners are noisy, so the numbers are only comparable within a run (baseline vs instrumented), and Express is measured over loopback, which makes its p99 unreliable.

---

## What I'd do differently in v2

- **Configurable anomaly threshold per service** — the global z > 4.0 threshold (with count-based error tests and minimum effect sizes) keeps false positives near one every ~20 weeks, but low-volume services need large shifts to clear it. Per-service thresholds or adaptive thresholds based on historical false-positive rates would improve signal quality.
- **Structured logging from the SDK** — currently the SDK uses Python's stdlib `logging` at WARNING level. Structured JSON logs (with service name, event counts, error types) would make SDK health far easier to observe in a log aggregator.
- **Collector connection pooling configuration exposed** — the asyncpg pool min/max sizes are env vars but not documented in the SDK README. Under high ingest volume, pool exhaustion is a silent failure; exposing this more clearly would help operators.
- **Design for several collector instances from the start** — alert/SLO jobs, rate limits and the key and service caches are per process. State is already in Postgres; leader election for the jobs and a shared rate limiter would let the collector scale out.

---

## Explicit non-decisions (deferred to v2)

| Feature | Why deferred |
|---|---|
| Real-time streaming (WebSockets/SSE) | 30–60 s polling is enough for the observability use case; persistent connections add complexity to the collector and ops burden |
| Trace views (span waterfalls) | OTLP spans are reduced to request events; a trace UI is a different product and storage model |
| OIDC / SSO sign-in | Username + password covers self-hosted teams today; OIDC when a real deployment needs it |
| Node SDK for Koa, NestJS, plain `http` | OpenTelemetry covers them; built when a real user asks |
| Helm chart | Docker Compose and single-container deploys cover current users |

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
