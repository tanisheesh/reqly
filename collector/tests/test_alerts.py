import asyncio
import json

import httpx

from app.alerts import notifier

DETAILS = {
    "route": "/orders",
    "day_of_week": "Thursday",
    "hour_range": "08:00-09:00",
    "observed_error_rate": 0.344,
    "baseline_error_rate": 0.031,
    "observed_p95_ms": 5138.5,
    "baseline_p95_ms": 2078.9,
    "z_score": 9.12,
    "window_start": "2026-10-08T08:00:00+00:00",
    "release_context": {
        "release": "v2",
        "release_first_seen_at": "2026-10-08T03:00:07+00:00",
        "is_new_release": True,
        "previous_release": "v1",
        "before": {"requests": 900, "error_rate": 0.031, "p95_ms": 2078.9},
        "after": {"requests": 400, "error_rate": 0.344, "p95_ms": 5138.5},
    },
    "hints": [{"dimension": "host", "value": "pod-3",
               "text": "90% of errors came from host pod-3, which served 33% of requests"}],
}
ALERT = {"id": 1, "service_name": "checkout-api", "route": "/orders", "details": DETAILS}


def test_opened_message_has_stats_release_and_hints():
    text = notifier.format_text(notifier.OPENED, "checkout-api", "/orders", DETAILS)
    assert text.splitlines() == [
        "🔴 *Anomaly* — `checkout-api` `/orders` (Thursday 08:00-09:00 UTC, z=9.12)",
        "• error rate 34.4% vs 3.1% usual · p95 5138ms vs 2079ms usual",
        "• running release `v2`, first seen 2026-10-08T03:00:07+00:00 — vs `v1`: "
        "errors 3.1% → 34.4%, p95 2079ms → 5138ms",
        "• 90% of errors came from host pod-3, which served 33% of requests",
    ]


def test_resolved_message():
    text = notifier.format_text(notifier.RESOLVED, "checkout-api", "/orders", DETAILS)
    assert text == "✅ *Resolved* — `checkout-api` `/orders` is back within its normal range."


def test_payload_shape_per_channel():
    channels = notifier.Channels(
        slack_webhook_url="https://hooks.slack.test/x",
        discord_webhook_url="https://discord.test/y",
        webhook_url="https://example.test/z",
        dashboard_url="https://dash.test",
    )
    payloads = dict(notifier.build_payloads(channels, notifier.OPENED, ALERT))
    assert payloads["https://hooks.slack.test/x"]["text"].endswith("<https://dash.test|Open dashboard>")
    assert "Open dashboard" not in payloads["https://discord.test/y"]["content"]
    generic = payloads["https://example.test/z"]
    assert generic["event"] == "alert.opened" and generic["alert"]["route"] == "/orders"
    json.dumps(generic)  # must be JSON-serializable as sent


def test_no_channels_configured_sends_nothing():
    assert notifier.build_payloads(notifier.Channels(), notifier.OPENED, ALERT) == []


def test_one_failing_channel_does_not_stop_the_others():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(500 if request.url.host == "hooks.slack.test" else 200)

    channels = notifier.Channels(
        slack_webhook_url="https://hooks.slack.test/x", webhook_url="https://example.test/z"
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await notifier.send(channels, notifier.OPENED, ALERT, client=client)

    assert asyncio.run(run()) == 1
    assert seen == ["hooks.slack.test", "example.test"]
