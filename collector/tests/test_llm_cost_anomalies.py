"""LLM cost anomaly detection (pure) and its alert message."""

from datetime import datetime, timedelta, timezone

from app.alerts import notifier
from app.llm.anomalies import detect_cost_anomalies
from app.llm.prices import Price, PriceTable

PRICES = PriceTable({"gpt-4o-mini": Price(0.15, 0.60), "gpt-4o": Price(2.50, 10.00)}, as_of="test")
HOUR = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)  # a Monday
MIN_USD = 1.0


def _slot(weeks_ago, route="/chat", requests=1000, model="gpt-4o-mini", tokens_in=1500, tokens_out=300,
          llm_share=1.0):
    """llm_usage_1hour rows for one route-hour: `requests` requests, of which
    llm_share called `model` with the given tokens per call."""
    bucket = HOUR - timedelta(weeks=weeks_ago)
    calls = int(requests * llm_share)
    rows = [{"bucket": bucket, "route": route, "llm_model": model, "request_count": calls,
             "input_tokens": calls * tokens_in, "output_tokens": calls * tokens_out}]
    if requests > calls:
        rows.append({"bucket": bucket, "route": route, "llm_model": None, "request_count": requests - calls,
                     "input_tokens": 0, "output_tokens": 0})
    return rows


def _history(**current):
    """8 normal baseline weeks (with a little noise) plus the hour under test."""
    rows = []
    for k, jitter in zip(range(1, 9), (0.97, 1.02, 1.0, 0.99, 1.03, 0.98, 1.01, 1.0)):
        rows += _slot(k, requests=int(1000 * jitter))
    return rows + _slot(0, **current)


# normal hour: 1000 calls x (1500 x 0.15 + 300 x 0.60) / 1M = $0.405


def test_normal_hour_is_quiet():
    assert detect_cost_anomalies(_history(), HOUR, PRICES, MIN_USD) == []


def test_prompt_bloat_is_a_unit_cost_anomaly_with_tokens_as_the_driver():
    [f] = detect_cost_anomalies(_history(tokens_in=9000), HOUR, PRICES, MIN_USD)
    assert f["kind"] == "llm_cost"
    assert f["route"] == "/chat"
    assert f["cause"] == "unit_cost"
    assert f["day_of_week"] == "Monday" and f["hour_range"] == "14:00-15:00"
    assert f["observed_cost_usd"] == 1.53  # 1000 x (9000 x 0.15 + 300 x 0.60) / 1M
    assert f["extra_cost_usd"] > 1.0
    assert f["drivers"][0]["factor"] == "tokens_per_call"
    assert f["drivers"][0]["text"] == "tokens per model call 5.2× (1,800 → 9,300)"
    assert f["model_mix"] is None  # still the same model


def test_switch_to_a_pricier_model_names_the_model():
    [f] = detect_cost_anomalies(_history(model="gpt-4o"), HOUR, PRICES, MIN_USD)
    assert f["cause"] == "unit_cost"
    assert f["drivers"][0]["factor"] == "usd_per_1m_tokens"
    assert f["model_mix"]["model"] == "gpt-4o"
    assert f["model_mix"]["usual_model"] == "gpt-4o-mini"


def test_traffic_surge_is_a_volume_anomaly():
    [f] = detect_cost_anomalies(_history(requests=6000), HOUR, PRICES, MIN_USD)
    assert f["cause"] == "volume"
    assert f["drivers"][0]["factor"] == "requests"


def test_small_absolute_change_stays_quiet():
    # cost per request triples, but it's a cheap route: +$0.027 an hour
    rows = []
    for k in range(1, 9):
        rows += _slot(k, requests=100, tokens_in=500, tokens_out=100)
    rows += _slot(0, requests=100, tokens_in=1500, tokens_out=300)
    assert detect_cost_anomalies(rows, HOUR, PRICES, MIN_USD) == []
    # the same change on a lower floor is reported
    assert len(detect_cost_anomalies(rows, HOUR, PRICES, min_usd=0.02)) == 1


def test_needs_enough_baseline_hours():
    rows = _slot(1) + _slot(2) + _slot(0, tokens_in=9000)
    assert detect_cost_anomalies(rows, HOUR, PRICES, MIN_USD) == []
    assert len(detect_cost_anomalies(rows, HOUR, PRICES, MIN_USD, min_samples=2)) == 1


def test_route_that_started_calling_a_model():
    # the route existed for weeks without LLM calls; now every request calls one
    rows = []
    for k in range(1, 9):
        rows += _slot(k, llm_share=0.0)
    rows += _slot(0, tokens_in=8000)
    [f] = detect_cost_anomalies(rows, HOUR, PRICES, MIN_USD)
    assert f["cause"] == "unit_cost"
    assert f["baseline_cost_usd"] == 0
    assert f["model_mix"]["usual_model"] is None


def test_unpriced_models_cost_nothing():
    assert detect_cost_anomalies(_history(model="some-local-llama", tokens_in=50000), HOUR, PRICES, MIN_USD) == []


def test_routes_are_independent_and_sorted_by_extra_cost():
    rows = _history(tokens_in=9000)
    for k in range(1, 9):
        rows += _slot(k, route="/summarize")
    rows += _slot(0, route="/summarize", model="gpt-4o")
    findings = detect_cost_anomalies(rows, HOUR, PRICES, MIN_USD)
    assert [f["route"] for f in findings] == ["/summarize", "/chat"]


def test_alert_message():
    [f] = detect_cost_anomalies(_history(tokens_in=9000), HOUR, PRICES, MIN_USD)
    lines = notifier.format_text(notifier.OPENED, "checkout-api", "/chat", f).splitlines()
    assert lines[0] == "💸 *LLM cost spike* — `checkout-api` `/chat` (Monday 14:00-15:00 UTC)"
    assert lines[1].startswith("• $1.53 this hour vs $0.405 usual (+$1.12)")
    assert lines[2] == "• driven by cost per request"
    assert lines[3] == "• tokens per model call 5.2× (1,800 → 9,300)"
    resolved = notifier.format_text(notifier.RESOLVED, "checkout-api", "/chat", f)
    assert resolved == "✅ *Resolved* — LLM cost on `checkout-api` `/chat` is back within its normal range."
