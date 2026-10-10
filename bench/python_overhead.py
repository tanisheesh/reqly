"""How much time does the Reqly Python SDK add to each request?

Calls the app directly -- ASGI apps through their ASGI callable, Flask
through WSGI -- so the number is the SDK's own cost, not network noise. The
instrumented app ships to a local stand-in collector the whole time, so the
background flush thread's share of CPU (and the GIL) is included.

Baseline and instrumented runs alternate in rounds, so machine drift hits
both equally. Usage (from the repo root, with the SDK's dev dependencies):

    python bench/python_overhead.py                  # all frameworks
    python bench/python_overhead.py --requests 20000 --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk"))
import reqly  # noqa: E402


# --- stand-in collector ----------------------------------------------------------

class _Sink(BaseHTTPRequestHandler):
    batches = 0

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Sink.batches += 1
        self.send_response(202)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def start_collector() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


# --- apps ------------------------------------------------------------------------

def fastapi_app():
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: int):
        return {"id": item_id, "name": "widget"}

    return app


def starlette_app():
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def item(request):
        return JSONResponse({"id": int(request.path_params["item_id"]), "name": "widget"})

    return Starlette(routes=[Route("/items/{item_id:int}", item)])


def flask_app():
    from flask import Flask

    app = Flask(__name__)

    @app.get("/items/<int:item_id>")
    def item(item_id):
        return {"id": item_id, "name": "widget"}

    return app


FRAMEWORKS = {"fastapi": ("asgi", fastapi_app), "starlette": ("asgi", starlette_app), "flask": ("wsgi", flask_app)}


# --- drivers: one request, timed ----------------------------------------------------

def asgi_driver(app):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": "/items/42", "raw_path": b"/items/42", "root_path": "",
        "query_string": b"", "headers": [(b"host", b"bench"), (b"x-api-key", b"k1")],
        "client": ("127.0.0.1", 5000), "server": ("bench", 80),
    }
    loop = asyncio.new_event_loop()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 200, message

    async def one():
        await app(dict(scope), receive, send)

    def run(n: int) -> list[int]:
        async def batch():
            out = []
            for _ in range(n):
                t = time.perf_counter_ns()
                await one()
                out.append(time.perf_counter_ns() - t)
            return out

        return loop.run_until_complete(batch())

    return run


def wsgi_driver(app):
    from werkzeug.test import EnvironBuilder

    environ = EnvironBuilder(path="/items/42", headers={"X-Api-Key": "k1"}).get_environ()

    def start_response(status, headers, exc_info=None):
        assert status.startswith("200"), status

    def run(n: int) -> list[int]:
        out = []
        for _ in range(n):
            t = time.perf_counter_ns()
            result = app(dict(environ), start_response)
            for _chunk in result:
                pass
            close = getattr(result, "close", None)
            if close:
                close()
            out.append(time.perf_counter_ns() - t)
        return out

    return run


# --- measurement -------------------------------------------------------------------

def summarize(samples_ns: list[int]) -> dict:
    us = sorted(s / 1000 for s in samples_ns)
    return {
        "mean_us": round(statistics.fmean(us), 2),
        "p50_us": round(us[len(us) // 2], 2),
        "p99_us": round(us[int(len(us) * 0.99)], 2),
    }


def bench_framework(name: str, requests: int, rounds: int, collector: str) -> dict:
    kind, factory = FRAMEWORKS[name]
    driver = asgi_driver if kind == "asgi" else wsgi_driver
    baseline_app = factory()
    instrumented_app = factory()
    client = reqly.instrument(
        instrumented_app, service_name=f"bench-{name}", collector_url=collector, api_key="k",
        flush_interval_seconds=1.0, max_queue_size=100_000, consumer_header="x-api-key", consumer_salt="bench",
    )
    assert client is not None
    run_base, run_reqly = driver(baseline_app), driver(instrumented_app)

    per_round = max(1, requests // rounds)
    run_base(500)  # warm-up: imports, caches, JIT-less but still
    run_reqly(500)
    base, inst = [], []
    for _ in range(rounds):
        base += run_base(per_round)
        inst += run_reqly(per_round)
    client._buffer.flush()
    stats = client.stats()
    client.shutdown()

    b, r = summarize(base), summarize(inst)
    return {
        "framework": name,
        "requests": len(base),
        "baseline": b,
        "reqly": r,
        "added_us": {k.replace("_us", ""): round(r[k] - b[k], 2) for k in b},
        "events_shipped": stats.get("shipped_events"),
        "events_dropped": stats.get("dropped_events"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frameworks", nargs="*", default=list(FRAMEWORKS))
    parser.add_argument("--requests", type=int, default=20_000, help="per variant")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--json", help="also write the results here")
    args = parser.parse_args()

    collector = start_collector()
    results = [bench_framework(f, args.requests, args.rounds, collector) for f in args.frameworks]
    env = {
        "python": platform.python_version(), "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(), "reqly": reqly.__version__,
    }

    print(f"Reqly Python SDK overhead -- Python {env['python']}, {env['platform']}")
    print(f"{args.requests} requests per variant, {args.rounds} alternating rounds, in-process\n")
    print("| framework | baseline p50 | with Reqly p50 | added p50 | added mean | added p99 |")
    print("|---|---|---|---|---|---|")
    def signed(v: float) -> str:
        return f"+{v} µs" if v >= 0 else f"−{-v} µs (noise)"

    for r in results:
        added = r["added_us"]
        print(
            f"| {r['framework']} | {r['baseline']['p50_us']} µs | {r['reqly']['p50_us']} µs | "
            f"**{signed(added['p50'])}** | {signed(added['mean'])} | {signed(added['p99'])} |"
        )
    shipped = sum(r["events_shipped"] or 0 for r in results)
    print(f"\nEvents shipped to the stand-in collector during the run: {shipped}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"env": env, "results": results}, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
