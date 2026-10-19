"""4xx spike detection (pure) and its alert message."""

from datetime import datetime, timedelta, timezone

from app.alerts import notifier
from app.alerts.client_errors import detect_client_error_spikes

HOUR = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)  # a Thursday


def _rows(route, hour_counts):
    """hour_counts: {hour: {status: count}}"""
    return [
        {"bucket": hour, "route": route, "status_code": status, "count": count}
        for hour, by_status in hour_counts.items() for status, count in by_status.items()
    ]


def _weeks(current, usual, weeks=8):
    data = {HOUR: current}
    data.update({HOUR - timedelta(weeks=k): usual for k in range(1, weeks + 1)})
    return data


def test_a_401_wave_after_a_deploy_is_found():
    rows = _rows("/orders", _weeks({200: 600, 401: 400, 422: 90}, {200: 980, 401: 10, 422: 10}))
    rows += _rows("/health", _weeks({200: 1000}, {200: 1000}))
    [finding] = detect_client_error_spikes(rows, HOUR)
    assert (finding["route"], finding["status_code"], finding["observed_count"], finding["requests"]) == \
        ("/orders", 401, 400, 1090)
    assert finding["observed_share"] == round(400 / 1090, 4) and finding["baseline_share"] == 0.01
    assert [s["status_code"] for s in finding["statuses"]] == [401, 422]  # 422 spiked too
    assert (finding["kind"], finding["day_of_week"], finding["hour_range"]) == ("client_errors", "Thursday", "09:00-10:00")


def test_usual_levels_small_counts_and_unwatched_codes_are_not_spikes():
    rows = _rows("/login", _weeks({200: 900, 401: 100}, {200: 900, 401: 100}))  # always 10% 401
    rows += _rows("/a", _weeks({200: 50, 403: 15}, {200: 65}))  # only 15: below MIN_COUNT
    rows += _rows("/b", _weeks({200: 500, 404: 500}, {200: 1000}))  # 404 isn't watched
    rows += _rows("/c", _weeks({200: 500, 429: 500}, {200: 1000}, weeks=2))  # 2 baseline weeks: too thin
    assert detect_client_error_spikes(rows, HOUR) == []


def test_watched_codes_are_configurable():
    rows = _rows("/b", _weeks({200: 500, 404: 500}, {200: 1000}))
    assert [f["status_code"] for f in detect_client_error_spikes(rows, HOUR, statuses=(404,))] == [404]


def test_message():
    rows = _rows("/orders", _weeks({200: 600, 401: 400}, {200: 990, 401: 10}))
    [finding] = detect_client_error_spikes(rows, HOUR)
    finding["release_context"] = {"release": "v7", "release_first_seen_at": "2026-10-15T08:40:00+00:00",
                                  "is_new_release": True, "previous_release": "v6"}
    lines = notifier.format_text(notifier.OPENED, "shop", "/orders", finding).splitlines()
    assert lines[0].startswith("🟡 *Client error spike* — `shop` `/orders` (Thursday 09:00-10:00 UTC, z=")
    assert lines[1] == "• 401 on 40.0% of requests vs 1.0% usual (400 of 1,000)"
    assert lines[2].startswith("• running release `v7`")
    assert notifier.format_text(notifier.RESOLVED, "shop", "/orders", finding) == \
        "✅ *Resolved* — `shop` `/orders` client errors are back to normal."
