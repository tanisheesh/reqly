import pytest
from fastapi.testclient import TestClient

from app.alerts import notifier
from app.main import app
from app.slo.model import (
    BUDGET_EXHAUSTED, FAST_BURN, NO_DATA, OK, SLOW_BURN, Counts, burn_rate, evaluate,
)


def _short(**overrides):
    base = {name: Counts(bad=0, total=1000) for name in ("5m", "30m", "1h", "6h")}
    base.update(overrides)
    return base


def test_burn_rate_is_bad_share_over_allowed_share():
    # 1% bad against a 99% target spends budget exactly as fast as allowed
    assert burn_rate(Counts(bad=10, total=1000), 0.99) == pytest.approx(1.0)
    assert burn_rate(Counts(bad=144, total=1000), 0.99) == pytest.approx(14.4)
    assert burn_rate(Counts(bad=0, total=0), 0.99) == 0.0


def test_healthy_slo_with_most_of_its_budget_left():
    status = evaluate(0.99, Counts(bad=250, total=100_000), _short())
    assert status["state"] == OK
    assert status["sli"] == pytest.approx(0.9975)
    assert status["budget_remaining"] == pytest.approx(0.75)  # 250 of 1000 allowed spent


def test_fast_burn_needs_both_the_1h_and_5m_windows():
    hot = Counts(bad=200, total=1000)  # 20% bad on a 99% SLO = 20x burn
    assert evaluate(0.99, Counts(bad=300, total=100_000), _short(**{"1h": hot, "5m": hot}))["state"] == FAST_BURN
    # burned an hour ago but recovered in the last 5 minutes: no page
    assert evaluate(0.99, Counts(bad=300, total=100_000), _short(**{"1h": hot}))["state"] == OK


def test_slow_burn():
    warm = Counts(bad=80, total=1000)  # 8x
    status = evaluate(0.99, Counts(bad=300, total=100_000), _short(**{"6h": warm, "30m": warm}))
    assert status["state"] == SLOW_BURN


def test_tiny_traffic_does_not_burn():
    noisy = Counts(bad=3, total=10)  # 30x, but only 10 requests
    assert evaluate(0.99, Counts(bad=3, total=10_000), _short(**{"1h": noisy, "5m": noisy}))["state"] == OK


def test_exhausted_budget_and_no_data():
    assert evaluate(0.99, Counts(bad=1500, total=100_000), _short())["state"] == BUDGET_EXHAUSTED
    status = evaluate(0.99, Counts(bad=0, total=0), _short())
    assert status["state"] == NO_DATA and status["sli"] is None


def test_slo_alert_message():
    details = {
        "kind": "slo",
        "slo": {"name": "orders availability", "route": "/orders", "objective": "availability",
                "target": 0.99, "latency_threshold_ms": None, "window_days": 28},
        "status": {"state": FAST_BURN, "sli": 0.9712, "budget_remaining": -0.12,
                   "burn_rates": {"5m": 31.0, "30m": 28.5, "1h": 26.2, "6h": 19.0}},
    }
    text = notifier.format_text(notifier.OPENED, "flask-demo", "slo:orders availability", details)
    assert text.splitlines() == [
        "🔥 *Fast burn* — SLO `orders availability` on `flask-demo` `/orders` "
        "(99.00% of requests without errors, 28d)",
        "• burning error budget 26.2× (1h) / 31× (5m) / 19× (6h) the sustainable rate",
        "• SLI 97.120% · 0% of the error budget left",
    ]
    resolved = notifier.format_text(notifier.RESOLVED, "flask-demo", "slo:orders availability", details)
    assert resolved == "✅ *Resolved* — SLO `orders availability` (`flask-demo`) is no longer burning its error budget."


def test_slo_management_needs_the_ingest_key_and_valid_input(monkeypatch):
    from app.config import settings

    client = TestClient(app)
    body = {"service_name": "svc", "name": "p95", "objective": "latency", "target": 0.95}
    assert client.put("/v1/slos", json=body, headers={"X-Reqly-Key": settings.read_key}).status_code == 403  # a valid key without the admin scope
    # latency SLO without a threshold
    assert client.put("/v1/slos", json=body, headers={"X-Reqly-Key": settings.ingest_key}).status_code == 422
    bad_target = {**body, "objective": "availability", "target": 1.0}
    assert client.put("/v1/slos", json=bad_target, headers={"X-Reqly-Key": settings.ingest_key}).status_code == 422
