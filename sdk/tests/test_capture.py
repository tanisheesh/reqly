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
