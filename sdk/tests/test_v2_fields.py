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
