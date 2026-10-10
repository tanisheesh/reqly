"""The weekly report's API surface (OpenAPI drift) section."""

import asyncio
import dataclasses

from app.insights import groq_client
from app.insights.report import fallback_report, summarize_drift

DRIFT = {
    "window_days": 30,
    "coverage": 0.8,
    "total_requests": 1000,
    "undocumented_requests": 120,
    "undocumented": [
        {"method": "POST", "route": "/internal/refund", "requests": 100, "error_rate": 0.0, "last_seen": None},
        {"method": "GET", "route": "/debug", "requests": 20, "error_rate": 0.0, "last_seen": None},
    ],
    "dead": [{"method": "GET", "path": "/v1/legacy/export"}],
    "deprecated_in_use": [
        {"method": "GET", "path": "/products", "requests": 70, "routes": ["/products"], "last_seen": None,
         "consumers": [{"consumer_id": "umbrella-partner", "requests": 49, "last_seen": None}]},
    ],
}


def test_summary_keeps_counts_and_the_top_items():
    s = summarize_drift(DRIFT)
    assert (s["undocumented_count"], s["unused_count"], s["deprecated_in_use_count"]) == (2, 1, 1)
    assert s["undocumented_traffic_share"] == 0.12
    assert s["top_undocumented"][0] == {"method": "POST", "route": "/internal/refund", "requests": 100}
    assert s["deprecated_in_use"][0]["consumers"] == ["umbrella-partner"]


def test_no_spec_or_nothing_to_report_gives_no_section():
    assert summarize_drift(None) is None
    clean = {**DRIFT, "undocumented": [], "dead": [], "deprecated_in_use": [], "undocumented_requests": 0}
    assert summarize_drift(clean) is None


def test_fallback_report_has_an_api_surface_section():
    text = fallback_report("shop", "2026-10-05", [], summarize_drift(DRIFT))
    assert "- No significant anomalies this week." in text
    assert ("- API surface (last 30 days): 2 undocumented endpoint(s) with traffic (12.0% of requests), "
            "1 documented endpoint(s) never called, 1 deprecated endpoint(s) still called") in text
    assert "  - deprecated but called: `GET /products` (70 requests by `umbrella-partner`)" in text


def test_a_quiet_week_with_drift_still_gets_a_report(monkeypatch):
    monkeypatch.setattr(groq_client, "settings", dataclasses.replace(groq_client.settings, groq_api_key=None))
    text = asyncio.run(groq_client.generate_report("shop", "2026-10-05", [], summarize_drift(DRIFT)))
    assert "API surface" in text
    quiet = asyncio.run(groq_client.generate_report("shop", "2026-10-05", [], None))
    assert quiet.startswith("No significant anomalies")
