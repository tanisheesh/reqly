import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import reqly


def test_instrument_fastapi_ships_normalized_route(fake_collector):
    collector_url, batches = fake_collector

    app = FastAPI()

    @app.get("/users/{user_id}")
    def get_user(user_id: int):
        return {"id": user_id}

    client = reqly.instrument(
        app,
        service_name="test-service",
        collector_url=collector_url,
        flush_interval_seconds=999,
        sample_rate=1.0,
    )
    assert client is not None

    test_client = TestClient(app)
    response = test_client.get("/users/123")
    assert response.status_code == 200

    client._buffer.flush()
    time.sleep(0.1)

    assert len(batches) == 1
    events = batches[0]["events"]
    assert len(events) == 1
    assert events[0]["route"] == "/users/{user_id}"
    assert events[0]["status_code"] == 200
    assert events[0]["error"] is False

    client.shutdown()


def test_instrument_fastapi_marks_server_errors():
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise ValueError("kaboom")

    client = reqly.instrument(
        app,
        service_name="test-service",
        collector_url="http://127.0.0.1:1",  # unreachable, never blocks the app
        flush_interval_seconds=999,
    )
    test_client = TestClient(app, raise_server_exceptions=False)
    response = test_client.get("/boom")
    assert response.status_code == 500

    stats = client.stats()
    assert stats["disabled"] is False
    assert stats["queued_events"] == 1
    client.shutdown()
