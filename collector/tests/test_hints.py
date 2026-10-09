from app.insights.hints import Breakdown, error_mix_hints, traffic_hints


def _b(requests, errors=None, slow=None):
    return Breakdown(requests=requests, errors=errors or {}, slow=slow or {})


def test_errors_concentrated_on_a_minority_host():
    window = _b({"pod-1": 100, "pod-2": 100, "pod-3": 100}, errors={"pod-3": 18, "pod-1": 2})
    hints = traffic_hints("host", window)
    assert [h.value for h in hints] == ["pod-3"]
    assert hints[0].text == "90% of errors came from host pod-3, which served 33% of requests"


def test_host_serving_most_traffic_is_not_a_lead():
    window = _b({"pod-1": 900, "pod-2": 100}, errors={"pod-1": 9, "pod-2": 1})
    assert traffic_hints("host", window) == []


def test_too_few_errors_gives_no_hint():
    window = _b({"pod-1": 100, "pod-2": 100}, errors={"pod-2": 3})
    assert traffic_hints("host", window) == []


def test_slow_requests_concentrated_on_a_host():
    window = _b({"pod-1": 100, "pod-2": 100, "pod-3": 100}, slow={"pod-2": 40, "pod-1": 2})
    hints = traffic_hints("host", window)
    assert len(hints) == 1 and "slow requests" in hints[0].text and hints[0].value == "pod-2"


def test_new_error_type():
    window = _b({}, errors={"TimeoutError": 9, "KeyError": 1})
    baseline = _b({}, errors={"KeyError": 20})
    hints = error_mix_hints("error_type", window, baseline)
    assert hints[0].text == "90% of errors are error type TimeoutError (not seen in the previous 7 days)"


def test_error_type_that_always_dominates_is_not_a_lead():
    window = _b({}, errors={"KeyError": 9, "ValueError": 1})
    baseline = _b({}, errors={"KeyError": 80, "ValueError": 20})
    assert error_mix_hints("error_type", window, baseline) == []


def test_status_code_shift_against_baseline():
    window = _b({}, errors={"503": 16, "500": 4})
    baseline = _b({}, errors={"500": 90, "503": 10})
    hints = error_mix_hints("status_code", window, baseline)
    assert hints[0].text == "80% of errors are status 503 (vs 10% in the previous 7 days)"
