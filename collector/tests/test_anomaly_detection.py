from datetime import datetime, timedelta, timezone

from app.insights.anomaly_detection import detect_anomalies


def _make_rows():
    """8 weeks of hourly data for one route, with a healthy baseline except
    the most recent week's Monday 08:00-09:00 bucket, which spikes error
    rate and p95 latency -- the flagship "/auth degrades every Monday
    morning" scenario.
    """
    rows = []
    now = datetime(2027, 2, 1, tzinfo=timezone.utc)  # a Monday
    for week_offset in range(8):
        for hour in range(24):
            bucket_time = now - timedelta(weeks=week_offset, hours=-hour)
            is_most_recent_week = week_offset == 0
            is_target_hour = bucket_time.weekday() == 0 and bucket_time.hour == 8
            if is_most_recent_week and is_target_hour:
                error_rate, p95_ms = 0.18, 1850.0
            else:
                error_rate, p95_ms = 0.02, 320.0
            rows.append(
                {
                    "bucket": bucket_time,
                    "route": "/auth",
                    "request_count": 100,
                    "error_count": int(error_rate * 100),
                    "error_rate": error_rate,
                    "p95_ms": p95_ms,
                }
            )
    return rows, now + timedelta(hours=1)


def test_detects_recurring_monday_morning_degradation():
    rows, now = _make_rows()
    anomalies = detect_anomalies(rows, now=now)
    assert len(anomalies) == 1
    anomaly = anomalies[0]
    assert anomaly.route == "/auth"
    assert anomaly.day_of_week == "Monday"
    assert anomaly.hour_range == "08:00-09:00"
    assert anomaly.observed_error_rate > anomaly.baseline_error_rate
    assert anomaly.z_score > 2.0


def test_no_anomalies_when_traffic_is_uniform():
    rows = []
    now = datetime(2027, 2, 1, tzinfo=timezone.utc)
    for week_offset in range(8):
        for hour in range(24):
            rows.append(
                {
                    "bucket": now - timedelta(weeks=week_offset, hours=-hour),
                    "route": "/users",
                    "request_count": 100,
                    "error_count": 2,
                    "error_rate": 0.02,
                    "p95_ms": 300.0,
                }
            )
    assert detect_anomalies(rows, now=now + timedelta(hours=1)) == []


def test_insufficient_baseline_data_is_not_flagged():
    # Only 1 week of history -- below MIN_BASELINE_SAMPLES, even though the
    # single data point looks anomalous, it must not be flagged.
    now = datetime(2027, 2, 1, tzinfo=timezone.utc)
    rows = [
        {
            "bucket": now - timedelta(hours=1),
            "route": "/auth",
            "request_count": 100,
            "error_count": 1,
            "error_rate": 0.01,
            "p95_ms": 300.0,
        },
        {
            "bucket": now,
            "route": "/auth",
            "request_count": 100,
            "error_count": 90,
            "error_rate": 0.90,
            "p95_ms": 5000.0,
        },
    ]
    assert detect_anomalies(rows, now=now + timedelta(hours=1)) == []
