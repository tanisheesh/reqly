---
title: SDK overhead
description: How much time the Reqly SDKs add to each request, and how it is measured.
---

# SDK overhead

How much time does instrumenting an app with Reqly add to each request?

| SDK | Framework | Added per request (p50) |
|---|---|---|
| Python | FastAPI | ~13 µs |
| Python | Starlette | ~15 µs |
| Python | Flask | ~33 µs |
| Node | Fastify | ~10 µs |
| Node | Hono | ~6 µs |
| Node | Express | ~27 µs |

For scale: one round trip over a local network is usually 100–500 µs, and a typical API
endpoint that touches a database takes milliseconds. Numbers are from a Windows 11 laptop
(Python 3.13, Node 26), 20,000 requests per variant; the
[benchmark workflow](https://github.com/tanisheesh/reqly/blob/main/.github/workflows/bench.yml) reruns them on GitHub's Linux runners
on every SDK change (results in each run's summary).

Re-checked for reqly 0.5.5 and reqly-node 0.3.2 by running them back to back with the releases
these numbers were first measured on (0.5.2 and 0.1.2): the same within noise.

## How it is measured

- **The SDK's own cost, not the network's.** Apps are called directly — ASGI apps through
  their ASGI callable, Flask through WSGI, Fastify with `inject`, Hono with `app.request` —
  so the numbers aren't buried in socket noise. Express has no in-process driver and is
  measured over loopback HTTP with a keep-alive connection, which makes its p99 noisy.
- **Same app, with and without Reqly**, a small JSON endpoint with a path parameter.
- **Realistic SDK settings:** consumer tracking on (`X-API-Key`, hashed), and the background
  shipper sending to a local stand-in collector during the whole run, so its share of CPU
  (and, in Python, the GIL) is in the numbers. Every recorded event is accounted for at the
  end (recorded = shipped, none dropped).
- **Alternating rounds:** baseline and instrumented runs take turns, so drift in the machine
  (turbo, other processes) hits both.

Read the "added" columns, not the absolute ones: the absolute latency is the framework's.

## Run it

```bash
# Python (from the repo root, with the SDK's dev dependencies)
pip install -e "./sdk[dev]"
python bench/python_overhead.py --requests 20000 --json python-overhead.json

# Node
cd sdk-node && npm ci && npm run build
node scripts/bench.mjs --requests 20000 --json node-overhead.json
```

## What made it fast

The first run of this benchmark found two problems, fixed in Python SDK 0.5.2 and
reqly-node 0.1.2:

- **Node: one HTTP call per request.** A flush kept sending until the queue was empty, so
  events arriving during a send each went out as a one-event batch — under steady traffic,
  a request to the collector for every request served (Express measured +330 µs per
  request before the fix and +27 µs after, and a collector would have rate-limited the
  app). A flush now sends what was queued when it started.
- **Work moved off the request path:** event ids and ISO timestamps are made on the flush
  thread, consumer ids are hashed once per distinct value (cached), and request headers are
  read lazily — one lookup for the consumer header instead of copying all of them. Python
  overhead dropped by about half (FastAPI +34 → +13 µs, Flask +63 → +33 µs).
