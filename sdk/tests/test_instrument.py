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


def test_flush_interval_from_either_sdks_env_var(monkeypatch):
    from reqly.core.config import Config

    def resolve():
        return Config.resolve(service_name="s", collector_url=None, api_key=None, sample_rate=None,
                              flush_interval_seconds=None, max_batch_size=None, max_queue_size=None,
                              ignore_routes=None, capture_request_body=None).flush_interval_seconds

    monkeypatch.delenv("REQLY_FLUSH_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("REQLY_FLUSH_INTERVAL_MS", raising=False)
    assert resolve() == 5.0
    monkeypatch.setenv("REQLY_FLUSH_INTERVAL_MS", "2500")  # the Node SDK's name
    assert resolve() == 2.5
    monkeypatch.setenv("REQLY_FLUSH_INTERVAL_SECONDS", "1")  # Python's own name wins
    assert resolve() == 1.0


def test_out_of_range_shipping_settings_are_clamped(monkeypatch):
    monkeypatch.setenv("REQLY_MAX_BATCH_SIZE", "5000")
    monkeypatch.setenv("REQLY_FLUSH_INTERVAL_SECONDS", "0")
    config = _resolve()
    assert config.max_batch_size == 1000  # the collector rejects larger batches whole
    assert config.flush_interval_seconds == 0.1  # 0 would spin the flush thread
    config = _resolve(max_batch_size=0, max_queue_size=0)
    assert config.max_batch_size == 1 and config.max_queue_size == 1
