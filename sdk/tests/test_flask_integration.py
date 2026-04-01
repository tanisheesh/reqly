import time

from flask import Flask

import reqly


def test_instrument_flask_ships_normalized_route(fake_collector):
    collector_url, batches = fake_collector

    app = Flask(__name__)

    @app.get("/users/<int:user_id>")
    def get_user(user_id):
        return {"id": user_id}

    client = reqly.instrument(
        app,
        service_name="test-service",
        collector_url=collector_url,
        flush_interval_seconds=999,
        sample_rate=1.0,
    )
    assert client is not None

    test_client = app.test_client()
    response = test_client.get("/users/123")
    assert response.status_code == 200

    client._buffer.flush()
    time.sleep(0.1)

    assert len(batches) == 1
    events = batches[0]["events"]
    assert len(events) == 1
    assert events[0]["route"] == "/users/<int:user_id>"
    assert events[0]["status_code"] == 200

    client.shutdown()


def test_instrument_flask_captures_unhandled_exception_via_teardown():
    app = Flask(__name__)
    app.config["TESTING"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/boom")
    def boom():
        raise ValueError("kaboom")

    client = reqly.instrument(
        app,
        service_name="test-service",
        collector_url="http://127.0.0.1:1",
        flush_interval_seconds=999,
    )
    test_client = app.test_client()
    response = test_client.get("/boom")
    assert response.status_code == 500

    stats = client.stats()
    assert stats["queued_events"] == 1
    client.shutdown()
