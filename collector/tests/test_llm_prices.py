from datetime import datetime, timezone

import pytest

from app.alerts import notifier
from app.llm import prices as prices_module
from app.llm.prices import Price, PriceTable, price_table
from app.llm.usage import summarize

TABLE = PriceTable({"gpt-4o": Price(2.5, 10), "gpt-4o-mini": Price(0.15, 0.6), "claude-sonnet-4": Price(3, 15)}, "2025-10")


@pytest.mark.parametrize("model, key", [
    ("gpt-4o", "gpt-4o"),
    ("gpt-4o-2024-08-06", "gpt-4o"),
    ("GPT-4o-mini-2024-07-18", "gpt-4o-mini"),  # longest prefix, any case
    ("openai/gpt-4o-mini", "gpt-4o-mini"),  # provider prefix
    ("claude-sonnet-4-20250514", "claude-sonnet-4"),
])
def test_lookup_by_longest_prefix(model, key):
    assert TABLE.lookup(model)[0] == key


@pytest.mark.parametrize("model", ["llama-3.3-70b", "", None, "my-gpt-4o"])
def test_unknown_models_are_unpriced(model):
    assert TABLE.lookup(model) is None


def test_cost_per_million_tokens():
    assert Price(2.5, 10).cost(1_000_000, 100_000) == pytest.approx(3.5)


def test_default_table_loads_and_can_be_overridden(tmp_path, monkeypatch):
    price_table.cache_clear()
    default = price_table()
    assert default.lookup("gpt-4o-mini")[1] == Price(0.15, 0.6) and default.as_of

    override = tmp_path / "prices.yaml"
    override.write_text("as_of: '2026-10'\nmodels:\n  gpt-4o-mini: {input: 0.1, output: 0.5}\n  in-house-7b: {input: 0, output: 0}\n  broken: {input: x}\n")
    monkeypatch.setenv("LLM_PRICES_FILE", str(override))
    price_table.cache_clear()
    try:
        table = price_table()
        assert table.lookup("gpt-4o-mini")[1] == Price(0.1, 0.5)  # overridden
        assert table.lookup("in-house-7b")[1] == Price(0, 0)  # added
        assert table.lookup("gpt-4o")[1] == Price(2.5, 10)  # kept
        assert table.lookup("broken") is None and table.as_of == "2026-10"
    finally:
        monkeypatch.delenv("LLM_PRICES_FILE")
        price_table.cache_clear()
    assert prices_module.DEFAULT_PRICES.exists()


def test_summarize_costs_per_route():
    day = datetime(2026, 10, 9, tzinfo=timezone.utc)
    rows = [
        {"route": "/chat", "llm_model": "gpt-4o", "requests": 100, "input_tokens": 200_000, "output_tokens": 50_000},
        {"route": "/chat", "llm_model": "gpt-4o-mini", "requests": 300, "input_tokens": 600_000, "output_tokens": 100_000},
        {"route": "/chat", "llm_model": None, "requests": 600, "input_tokens": 0, "output_tokens": 0},
        {"route": "/search", "llm_model": "local-llama", "requests": 50, "input_tokens": 10_000, "output_tokens": 1_000},
        {"route": "/health", "llm_model": None, "requests": 999, "input_tokens": 0, "output_tokens": 0},
    ]
    daily = [{"day": day, "llm_model": "gpt-4o", "input_tokens": 200_000, "output_tokens": 50_000}]
    out = summarize(rows, daily, TABLE)

    chat = out["routes"][0]
    assert chat["route"] == "/chat" and chat["requests"] == 1000 and chat["llm_requests"] == 400
    assert chat["cost_usd"] == pytest.approx((0.5 + 0.5) + (0.09 + 0.06))  # gpt-4o + gpt-4o-mini
    assert [m["model"] for m in chat["models"]] == ["gpt-4o", "gpt-4o-mini"]
    assert chat["cost_per_1k_requests"] == pytest.approx(1000 * chat["cost_usd"] / 1000)
    search = out["routes"][1]
    assert search["route"] == "/search" and search["cost_usd"] == 0 and search["models"][0]["cost_usd"] is None
    assert [r["route"] for r in out["routes"]] == ["/chat", "/search"]  # no LLM: not listed
    assert out["unpriced_models"] == [{"model": "local-llama", "tokens": 11_000}]
    assert out["totals"]["requests"] == 2049 and out["totals"]["llm_requests"] == 450
    assert out["daily"] == [{"day": day, "cost_usd": pytest.approx(1.0)}]
    assert out["prices_as_of"] == "2025-10"


def test_alert_text_names_affected_consumers():
    details = {
        "day_of_week": "Friday", "hour_range": "15:00-16:00", "z_score": 5.1,
        "observed_error_rate": 0.3, "baseline_error_rate": 0.02, "observed_p95_ms": 900, "baseline_p95_ms": 300,
        "affected_consumers": {"active": 12, "affected": 4, "top": [
            {"consumer_id": "a1b2", "requests": 90, "errors": 40}, {"consumer_id": "c3d4", "requests": 20, "errors": 5},
        ]},
    }
    text = notifier.format_text(notifier.OPENED, "shop", "/orders", details)
    assert "• 4 of 12 consumers got errors — most: `a1b2` (40), `c3d4` (5)" in text
