from reqly.core.capture import RequestEvent, build_event, normalize_route


def test_normalize_route_uses_matched_template():
    assert normalize_route("/users/123", "/users/{id}") == "/users/{id}"


def test_normalize_route_collapses_unmatched_paths():
    assert normalize_route("/users/123", None) == "__unmatched__"
    assert normalize_route("/anything/else", None) == "__unmatched__"


def test_build_event_round_trips_through_to_dict():
    event = build_event(
        service_name="checkout-api",
        method="GET",
        route="/users/{id}",
        status_code=200,
        duration_ms=42.5,
        error=False,
        error_type=None,
        sdk_version="0.1.0",
    )
    assert isinstance(event, RequestEvent)
    data = event.to_dict()
    assert data["service_name"] == "checkout-api"
    assert data["route"] == "/users/{id}"
    assert data["status_code"] == 200
    assert data["error"] is False
    assert "event_id" in data
    assert "timestamp" in data


def test_id_and_timestamp_are_made_once_at_serialization():
    from datetime import datetime, timezone

    from reqly.core.capture import RequestEvent

    event = RequestEvent(route="/a", recorded_at=1_760_000_000.25)
    assert event.event_id is None  # nothing formatted on the request path
    first, again = event.to_dict(), event.to_dict()
    assert first["event_id"] == again["event_id"]  # a retried batch resends the same id
    assert datetime.fromisoformat(first["timestamp"]) == datetime.fromtimestamp(1_760_000_000.25, timezone.utc)
    assert "recorded_at" not in first


def test_lazy_headers_look_up_one_name_and_list_all_on_demand():
    from reqly.core.request_context import LazyHeaders

    calls = []
    raw = {"X-Api-Key": "k1", "Accept": "*/*"}

    def get_one(name):
        calls.append(name)
        return {k.lower(): v for k, v in raw.items()}.get(name)

    headers = LazyHeaders(get_one, raw.items)
    assert headers.get("X-API-KEY") == "k1" and calls == ["x-api-key"]
    assert headers.get("missing", "d") == "d"
    assert dict(headers) == {"x-api-key": "k1", "accept": "*/*"}  # iteration collects everything once
    assert headers["accept"] == "*/*" and len(headers) == 2
