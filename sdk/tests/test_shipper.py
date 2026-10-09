import httpx
import pytest

from reqly.core import shipper as shipper_module
from reqly.core.capture import RequestEvent
from reqly.core.shipper import Shipper


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(shipper_module.time, "sleep", lambda _s: None)


def _shipper_with_responses(statuses):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(statuses[min(len(calls), len(statuses)) - 1])

    s = Shipper(collector_url="http://collector", api_key="k", service_name="svc", sdk_version="t")
    s._client = httpx.Client(base_url="http://collector", transport=httpx.MockTransport(handler))
    return s, calls


@pytest.mark.parametrize("status", [500, 502, 503, 408, 429])
def test_transient_errors_are_retried(status):
    s, calls = _shipper_with_responses([status, 200])
    assert s.send_batch([RequestEvent()]) is True
    assert len(calls) == 2
    assert s.dropped_batches == 0
    assert s.shipped_events == 1


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_client_errors_are_dropped_without_retry(status):
    s, calls = _shipper_with_responses([status])
    assert s.send_batch([RequestEvent()]) is False
    assert len(calls) == 1
    assert s.dropped_batches == 1


def test_persistent_5xx_drops_after_max_retries():
    s, calls = _shipper_with_responses([503])
    assert s.send_batch([RequestEvent()]) is False
    assert len(calls) == shipper_module._MAX_RETRIES
    assert s.dropped_batches == 1
