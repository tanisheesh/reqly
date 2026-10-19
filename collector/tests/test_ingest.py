import gzip
import json
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

    async def any_service(principal, service):
        return True  # project ownership is covered by tests/test_projects_db.py

    monkeypatch.setattr(ingest_module, "writable_service", any_service)
    monkeypatch.setattr(ingest_module, "insert_events", fake_insert_events)

    async def fake_record_deployments(pool, rows):
        test_client.deployment_rows.extend(rows)

    monkeypatch.setattr(ingest_module, "record_deployments", fake_record_deployments)

    test_client = TestClient(app)
    test_client.captured_rows = captured_rows
    test_client.deployment_rows = []
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
    assert (body["accepted"], body["rejected"]) == (1, 1)
    assert body["reasons"] == ["duration_ms: Value error, duration_ms out of plausible range"]
    assert len(client.captured_rows) == 1


def test_ingest_accepts_a_gzip_body(client):
    batch = {"service_name": "svc", "events": [_valid_event() for _ in range(3)]}
    response = client.post(
        "/v1/ingest",
        headers={"X-Reqly-Key": "demo-key", "Content-Type": "application/json", "Content-Encoding": "gzip"},
        content=gzip.compress(json.dumps(batch).encode()),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 3, "rejected": 0}


def test_ingest_rejects_bad_or_unknown_encodings(client):
    headers = {"X-Reqly-Key": "demo-key", "Content-Type": "application/json"}
    body = json.dumps({"service_name": "svc", "events": [_valid_event()]}).encode()
    r = client.post("/v1/ingest", headers={**headers, "Content-Encoding": "gzip"}, content=body)
    assert r.status_code == 400 and r.json()["detail"] == "invalid gzip body"
    r = client.post("/v1/ingest", headers={**headers, "Content-Encoding": "br"}, content=body)
    assert r.status_code == 415


def test_ingest_rejects_oversized_route(client):
    response = client.post(
        "/v1/ingest",
        headers={"X-Reqly-Key": "demo-key"},
        json={
            "service_name": "svc",
            "events": [_valid_event(), _valid_event(route="/" + "x" * 600)],
        },
    )
    assert response.status_code == 200
    assert (response.json()["accepted"], response.json()["rejected"]) == (1, 1)


def test_ingest_rejects_oversized_service_name(client):
    response = client.post(
        "/v1/ingest",
        headers={"X-Reqly-Key": "demo-key"},
        json={"service_name": "s" * 200, "events": [_valid_event()]},
    )
    assert response.status_code == 422


def test_read_key_cannot_ingest(client):
    from app.config import settings

    assert settings.read_key != settings.ingest_key
    response = client.post(
        "/v1/ingest",
        headers={"X-Reqly-Key": settings.read_key},
        json={"service_name": "svc", "events": [_valid_event()]},
    )
    assert response.status_code == 401


def _col(row, name):
    from app.db.queries import EVENT_COLUMNS

    return row[EVENT_COLUMNS.index(name)]


def test_batch_level_release_and_environment_apply_to_events(client):
    response = client.post(
        "/v1/ingest",
        headers={"X-Reqly-Key": "demo-key"},
        json={
            "service_name": "svc",
            "release": "a1b2c3",
            "environment": "prod",
            "events": [_valid_event(), _valid_event(release="override", environment="staging")],
        },
    )
    assert response.status_code == 200
    first, second = client.captured_rows
    assert (_col(first, "release"), _col(first, "environment")) == ("a1b2c3", "prod")
    assert (_col(second, "release"), _col(second, "environment")) == ("override", "staging")
    assert len(client.deployment_rows) == 2


def test_v1_events_without_new_fields_still_accepted(client):
    response = client.post(
        "/v1/ingest",
        headers={"X-Reqly-Key": "demo-key"},
        json={"service_name": "svc", "events": [_valid_event()]},
    )
    assert response.json() == {"accepted": 1, "rejected": 0}
    row = client.captured_rows[0]
    assert _col(row, "release") is None
    assert _col(row, "response_bytes") is None


def test_negative_byte_counts_reject_only_that_event(client):
    response = client.post(
        "/v1/ingest",
        headers={"X-Reqly-Key": "demo-key"},
        json={
            "service_name": "svc",
            "events": [_valid_event(response_bytes=512), _valid_event(response_bytes=-1)],
        },
    )
    assert (response.json()["accepted"], response.json()["rejected"]) == (1, 1)
    assert _col(client.captured_rows[0], "response_bytes") == 512


def test_old_and_future_events_are_rejected_unless_backfill(client):
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    old = _valid_event(timestamp=(now - timedelta(days=20)).isoformat())
    future = _valid_event(timestamp=(now + timedelta(hours=2)).isoformat())
    recent = _valid_event(timestamp=(now - timedelta(days=12)).isoformat())
    headers = {"X-Reqly-Key": "demo-key"}

    body = client.post("/v1/ingest", headers=headers,
                       json={"service_name": "svc", "events": [old, future, recent]}).json()
    assert (body["accepted"], body["rejected"]) == (1, 2)
    assert body["reasons"] == [
        "timestamp older than 13 days (send it as a backfill batch)",
        "timestamp is in the future",
    ]

    # an explicit backfill may import history (the future is still refused)
    body = client.post("/v1/ingest", headers=headers, json={
        "service_name": "svc", "backfill": True,
        "events": [_valid_event(timestamp=(now - timedelta(days=20)).isoformat()), future],
    }).json()
    assert (body["accepted"], body["rejected"]) == (1, 1)


def test_ingest_is_rate_limited_per_key_not_per_ip(client, monkeypatch):
    """Two services behind one IP each get their own budget; the env key has
    its own (larger) one. Requests here all come from the same test client IP."""
    from app import auth, rate_limit
    from app.config import settings

    async def no_cap(pool, service, pairs, limit):
        return [consumer for _, consumer in pairs]

    env_principal = auth._key_principal

    async def key_principal(key):  # any rqk_ key is a valid project key here
        if key and key.startswith("rqk_"):
            return auth.Principal("project-key", frozenset({"ingest"}), frozenset({1}), key_project_id=1)
        return await env_principal(key)

    monkeypatch.setattr(ingest_module.consumer_cap, "apply", no_cap)
    monkeypatch.setattr(auth, "_key_principal", key_principal)
    monkeypatch.setattr(rate_limit, "RATE_LIMIT", "2/minute")
    monkeypatch.setattr(rate_limit, "ENV_KEY_LIMIT", "3/minute")
    rate_limit.limiter.reset()

    def send(key):
        body = {"service_name": "svc", "events": [_valid_event()]}
        return client.post("/v1/ingest", json=body, headers={"X-Reqly-Key": key}).status_code

    try:
        assert [send("rqk_service_a") for _ in range(3)] == [200, 200, 429]  # key A: 2/minute
        assert [send("rqk_service_b") for _ in range(2)] == [200, 200]       # key B: own budget, same IP
        assert [send(settings.ingest_key) for _ in range(4)] == [200, 200, 200, 429]  # env key: 3/minute
    finally:
        rate_limit.limiter.reset()


def test_ingest_bucket_never_holds_the_raw_key():
    from starlette.requests import Request

    from app.rate_limit import ENV_KEY_BUCKET, ingest_key
    from app.config import settings

    def req(key):
        headers = [(b"x-reqly-key", key.encode())] if key else []
        return Request({"type": "http", "headers": headers, "client": ("10.0.0.7", 1)})

    assert ingest_key(req(settings.ingest_key)) == ENV_KEY_BUCKET
    assert ingest_key(req("")) == "ingest:ip:10.0.0.7"
    bucket = ingest_key(req("rqk_secret_value"))
    assert bucket.startswith("ingest:key:") and "secret" not in bucket
