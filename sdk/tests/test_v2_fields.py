import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from flask import Flask

import reqly
from reqly.core import config as config_module
from reqly.core.config import Config


def _resolve(**overrides):
    kwargs = dict(
        service_name="svc", collector_url=None, api_key=None, sample_rate=None,
        flush_interval_seconds=None, max_batch_size=None, max_queue_size=None,
        ignore_routes=None, capture_request_body=None,
    )
    kwargs.update(overrides)
    return Config.resolve(**kwargs)


@pytest.fixture
def clean_release_env(monkeypatch):
    for name in ("REQLY_RELEASE", "REQLY_ENVIRONMENT") + config_module._RELEASE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_release_resolution_order(clean_release_env):
    mp = clean_release_env
    assert _resolve().release is None

    mp.setenv("RENDER_GIT_COMMIT", "render-sha")
    assert _resolve().release == "render-sha"

    mp.setenv("GITHUB_SHA", "gh-sha")  # earlier in the platform list
    assert _resolve().release == "gh-sha"

    mp.setenv("REQLY_RELEASE", "explicit-env")
    assert _resolve().release == "explicit-env"

    assert _resolve(release="kwarg").release == "kwarg"


def test_environment_from_kwarg_or_env(clean_release_env):
    mp = clean_release_env
    assert _resolve().environment is None
    mp.setenv("REQLY_ENVIRONMENT", " prod ")
    assert _resolve().environment == "prod"
    assert _resolve(environment="staging").environment == "staging"


def test_release_is_truncated(clean_release_env):
    assert len(_resolve(release="x" * 500).release) == 128


def test_fastapi_ships_release_environment_and_byte_counts(fake_collector, clean_release_env):
    collector_url, batches = fake_collector
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        body = await request.body()
        return {"len": len(body)}

    client = reqly.instrument(
        app, service_name="svc", collector_url=collector_url,
        flush_interval_seconds=999, release="v2", environment="prod",
    )
    try:
        payload = b'{"hello": "world"}'
        response = TestClient(app).post(
            "/echo", content=payload, headers={"Content-Type": "application/json"}
        )
        client._buffer.flush()
        time.sleep(0.1)
    finally:
        client.shutdown()

    batch = batches[0]
    assert batch["release"] == "v2"
    assert batch["environment"] == "prod"
    event = batch["events"][0]
    assert event["request_bytes"] == len(payload)
    assert event["response_bytes"] == len(response.content)


def test_flask_ships_byte_counts(fake_collector, clean_release_env):
    collector_url, batches = fake_collector
    app = Flask(__name__)

    @app.post("/echo")
    def echo():
        return {"ok": True}

    client = reqly.instrument(
        app, service_name="svc", collector_url=collector_url, flush_interval_seconds=999
    )
    try:
        response = app.test_client().post("/echo", data=b"abcdef")
        client._buffer.flush()
        time.sleep(0.1)
    finally:
        client.shutdown()

    batch = batches[0]
    assert "release" not in batch  # nothing configured -> field omitted
    event = batch["events"][0]
    assert event["request_bytes"] == 6
    assert event["response_bytes"] == len(response.data)


def test_trace_ids_of_failed_and_slow_requests(fake_collector):
    from fastapi import HTTPException

    from reqly.core import traces

    url, batches = fake_collector
    app = FastAPI()

    @app.get("/ok")
    def ok():
        return {}

    @app.get("/denied")
    def denied():
        raise HTTPException(status_code=401)

    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999)
    http = TestClient(app)
    trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    http.get("/ok", headers={"traceparent": f"00-{trace}-00f067aa0ba902b7-01"})  # fast 200: not sent
    http.get("/denied", headers={"traceparent": f"00-{trace}-00f067aa0ba902b7-01"})
    http.get("/denied", headers={"sentry-trace": "771a43a4192642f0b136d5159a501700-b8d6b0c4d7b4a3f2-1"})
    http.get("/denied")
    client._buffer.flush()
    time.sleep(0.05)
    events = [e for b in batches for e in b["events"]]
    assert ["trace_id" in e for e in events] == [False, True, True, False]
    assert [e.get("trace_id") for e in events[1:3]] == [trace, "771a43a4192642f0b136d5159a501700"]
    assert traces.trace_id_from_headers({"x-b3-traceid": "463AC35C9F6413AD"}) == "463ac35c9f6413ad"
    assert traces.trace_id_from_headers({"traceparent": "00-" + "0" * 32 + "-00f067aa0ba902b7-01"}) is None
    client.shutdown()


def test_trace_slow_ms_is_configurable(fake_collector, monkeypatch):
    url, batches = fake_collector
    app = FastAPI()

    @app.get("/work")
    def work():
        time.sleep(0.25)
        return {}

    monkeypatch.setenv("REQLY_TRACE_SLOW_MS", "100")
    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999)
    assert client.config.trace_slow_ms == 100
    TestClient(app).get("/work", headers={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"})
    client._buffer.flush()
    time.sleep(0.05)
    [event] = [e for b in batches for e in b["events"]]
    assert event["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"  # 250 ms: slow enough at 100
    client.shutdown()
