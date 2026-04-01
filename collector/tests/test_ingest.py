import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import ingest as ingest_module


class FakePool:
    pass


@pytest.fixture
def client(monkeypatch):
    captured_rows = []

    async def fake_insert_events(pool, rows):
        captured_rows.extend(rows)

    monkeypatch.setattr(ingest_module, "get_pool", lambda: FakePool())
    monkeypatch.setattr(ingest_module, "insert_events", fake_insert_events)

    test_client = TestClient(app)
    test_client.captured_rows = captured_rows
    return test_client


def _valid_event(**overrides):
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": "GET",
        "route": "/users/{id}",
        "status_code": 200,
        "duration_ms": 42.5,
        "error": False,
        "error_type": None,
        "host": "container-1",
    }
    event.update(overrides)
    return event


def test_ingest_rejects_missing_api_key(client):
    response = client.post(
        "/v1/ingest", json={"service_name": "svc", "events": [_valid_event()]}
    )
    assert response.status_code == 401


def test_ingest_accepts_valid_batch(client):
    response = client.post(
        "/v1/ingest",
        headers={"X-Reqly-Key": "demo-key"},
        json={"service_name": "svc", "events": [_valid_event(), _valid_event()]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"accepted": 2, "rejected": 0}
    assert len(client.captured_rows) == 2


def test_ingest_partial_acceptance_drops_only_bad_events(client):
    good = _valid_event()
    bad = _valid_event(duration_ms=-5)  # fails clamp_duration validator
    response = client.post(
        "/v1/ingest",
        headers={"X-Reqly-Key": "demo-key"},
        json={"service_name": "svc", "events": [good, bad]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"accepted": 1, "rejected": 1}
    assert len(client.captured_rows) == 1
