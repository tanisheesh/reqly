from fastapi import FastAPI

import reqly
from reqly.core.config import Config


def _resolve(**overrides):
    kwargs = dict(
        service_name="svc", collector_url=None, api_key=None, sample_rate=None,
        flush_interval_seconds=None, max_batch_size=None, max_queue_size=None,
        ignore_routes=None, capture_request_body=None,
    )
    kwargs.update(overrides)
    return Config.resolve(**kwargs)


def test_instrument_twice_returns_same_client_and_adds_one_middleware(fake_collector):
    collector_url, _ = fake_collector
    app = FastAPI()
    first = reqly.instrument(app, service_name="svc", collector_url=collector_url)
    second = reqly.instrument(app, service_name="svc", collector_url=collector_url)
    try:
        assert first is not None
        assert second is first
        assert len(app.user_middleware) == 1
    finally:
        first.shutdown()


def test_ignore_routes_env_is_whitespace_tolerant(monkeypatch):
    monkeypatch.setenv("REQLY_IGNORE_ROUTES", "/health, /metrics ,,/ready")
    assert _resolve().ignore_routes == ["/health", "/metrics", "/ready"]
