"""End to end: both SDKs -> a real collector process -> TimescaleDB -> the
read API the dashboard uses.

Needs a TimescaleDB with the Toolkit (timescale/timescaledb-ha) at
DATABASE_URL, the collector's dependencies, the Python SDK installed, and the
Node SDK built (sdk-node/dist). Locally a missing piece skips the test; in CI
(CI=true) it fails instead, so nothing is skipped silently.

    pytest e2e
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://reqly:localdev@localhost:5432/reqly")
INGEST_KEY = "e2e-ingest-key"
READ_KEY = "e2e-read-key"
RUN = uuid.uuid4().hex[:8]
PY_SERVICE = f"e2e-py-{RUN}"
NODE_SERVICE = f"e2e-node-{RUN}"
RELEASE = f"e2e-{RUN}"
AGGREGATES = (
    "api_latency_1min", "route_latency_1min", "route_errors_1hour",
    "route_status_distribution_1hour", "consumer_usage_1hour", "llm_usage_1hour",
)


def _missing(reason: str) -> None:
    if os.environ.get("CI"):
        pytest.fail(reason)
    pytest.skip(reason)


async def _sql(*statements: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def collector():
    try:
        asyncio.run(_sql("SELECT 1"))
    except Exception as exc:
        _missing(f"TimescaleDB not reachable at DATABASE_URL: {exc}")
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": DATABASE_URL,
        "REQLY_INGEST_KEY": INGEST_KEY,
        "REQLY_READ_KEY": READ_KEY,
        "PUBLIC_DASHBOARD": "true",  # read with the read key, as the public demo does
        "INSIGHTS_SCHEDULER_ENABLED": "false",
        "ALERTS_ENABLED": "false",
        "LOG_FORMAT": "json",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT / "collector", env=env,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 90  # migrations on a fresh database
        while True:
            if process.poll() is not None:
                pytest.fail(f"collector exited with {process.returncode}")
            try:
                if httpx.get(f"{url}/v1/ready", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("collector not ready after 90s")
            time.sleep(0.5)
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        asyncio.run(_sql(
            f"DELETE FROM request_events WHERE service_name IN ('{PY_SERVICE}', '{NODE_SERVICE}')",
            f"DELETE FROM deployments WHERE service_name IN ('{PY_SERVICE}', '{NODE_SERVICE}')",
            f"DELETE FROM project_services WHERE service_name IN ('{PY_SERVICE}', '{NODE_SERVICE}')",
        ))


def _python_app_traffic(collector_url: str) -> None:
    """A FastAPI app instrumented with the Python SDK, driven in-process."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    import reqly

    app = FastAPI()

    @app.get("/orders/{order_id}")
    def order(order_id: int):
        return {"id": order_id}

    @app.get("/boom")
    def boom():
        raise HTTPException(status_code=503, detail="down")

    @app.post("/chat")
    def chat():
        reqly.record_llm_usage("gpt-4o-mini", input_tokens=1000, output_tokens=200)
        return {"ok": True}

    @app.post("/agent")
    def agent():  # two models in one request: each priced at its own rate
        reqly.record_llm_usage("gpt-4o", input_tokens=1000, output_tokens=300)
        reqly.record_llm_usage("gpt-4o-mini", input_tokens=400, output_tokens=40)
        return {"ok": True}

    client = reqly.instrument(
        app, service_name=PY_SERVICE, collector_url=collector_url, api_key=INGEST_KEY,
        release=RELEASE, environment="ci", consumer_header="x-api-key", consumer_salt="e2e-salt",
        flush_interval_seconds=60,  # everything goes out in one (gzipped) batch at shutdown
    )
    assert client is not None
    http = TestClient(app)
    for i in range(15):
        http.get(f"/orders/{i}", headers={"x-api-key": f"key-{i % 3}"})
    for _ in range(3):
        http.get("/boom")
    for _ in range(2):
        http.post("/chat")
    http.post("/agent")
    client.shutdown()
    assert client.stats()["shipped_events"] == 21, client.stats()


def _node_traffic(collector_url: str) -> None:
    node = shutil.which("node")
    if node is None or not (ROOT / "sdk-node" / "dist" / "esm" / "index.js").exists():
        _missing("node or the built Node SDK (sdk-node: npm run build) is missing")
    subprocess.run(
        [node, str(ROOT / "e2e" / "node_sender.mjs"), collector_url, INGEST_KEY, NODE_SERVICE, RELEASE],
        check=True, timeout=60,
    )


def _read(url: str, path: str, **params) -> dict:
    response = httpx.get(f"{url}{path}", params=params, headers={"X-Reqly-Key": READ_KEY}, timeout=10)
    assert response.status_code == 200, (path, response.status_code, response.text)
    return response.json()


def test_events_from_both_sdks_reach_the_dashboard_api(collector):
    _python_app_traffic(collector)
    _node_traffic(collector)
    # The policies materialize on their own schedule; refresh now instead of waiting.
    asyncio.run(_sql(*(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)" for view in AGGREGATES)))

    services = _read(collector, "/v1/services")["services"]
    assert PY_SERVICE in services and NODE_SERVICE in services

    summary = _read(collector, "/v1/metrics/summary", service_name=PY_SERVICE, window="1h")
    routes = {r["route"]: r for r in summary["top_routes"]}
    assert routes["/orders/{order_id}"]["request_count"] == 15
    assert routes["/boom"]["request_count"] == 3 and routes["/boom"]["error_rate"] == 1.0
    assert routes["/chat"]["request_count"] == 2
    assert {"status_code": 503, "count": 3} in summary["status_distribution"]

    assert RELEASE in [r["release"] for r in _read(collector, f"/v1/services/{PY_SERVICE}/releases")["releases"]]
    assert _read(collector, f"/v1/services/{PY_SERVICE}/environments")["environments"] == ["ci"]
    env_summary = _read(collector, "/v1/metrics/summary", service_name=PY_SERVICE, window="1h", environment="ci")
    assert sum(r["request_count"] for r in env_summary["top_routes"]) == 21

    consumers = _read(collector, f"/v1/services/{PY_SERVICE}/consumers", window="24h")
    assert (consumers["consumers"], consumers["requests_with_consumer"]) == (3, 15)  # key-0..2
    # hashed by the SDK before sending: no raw key reaches the collector
    assert all(len(c["consumer_id"]) == 16 and not c["consumer_id"].startswith("key-") for c in consumers["top"])

    llm = _read(collector, f"/v1/services/{PY_SERVICE}/llm-usage", window="24h")
    assert (llm["totals"]["input_tokens"], llm["totals"]["output_tokens"]) == (3400, 740)
    assert llm["totals"]["cost_usd"] > 0
    agent = next(r for r in llm["routes"] if r["route"] == "/agent")
    assert {m["model"]: (m["input_tokens"], m["output_tokens"]) for m in agent["models"]} == {
        "gpt-4o": (1000, 300), "gpt-4o-mini": (400, 40),
    }

    node = _read(collector, "/v1/metrics/summary", service_name=NODE_SERVICE, window="1h")
    node_routes = {r["route"]: r for r in node["top_routes"]}
    assert node_routes["/items/:id"]["request_count"] == 25
    assert node_routes["/items/:id"]["error_rate"] == 0.2  # 5 of 25 are 500s

    metrics = httpx.get(f"{collector}/metrics", timeout=10).text
    accepted = [line for line in metrics.splitlines()
                if line.startswith('reqly_ingest_events_total{outcome="accepted",source="sdk"}')]
    assert accepted and float(accepted[0].split()[-1]) >= 46
