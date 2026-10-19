import gzip
import json

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


def _big_batch():
    return [RequestEvent(route=f"/items/{i}") for i in range(20)]  # > _GZIP_MIN_BYTES as JSON


def test_large_batches_are_gzipped_small_ones_are_not():
    s, calls = _shipper_with_responses([200])
    assert s.send_batch(_big_batch()) is True
    assert calls[0].headers["content-encoding"] == "gzip"
    sent = json.loads(gzip.decompress(calls[0].content))
    assert sent["service_name"] == "svc" and len(sent["events"]) == 20

    assert s.send_batch([RequestEvent()]) is True
    assert "content-encoding" not in calls[1].headers
    assert len(json.loads(calls[1].content)["events"]) == 1


def test_a_collector_without_gzip_gets_plain_json_from_then_on():
    calls = []

    def old_collector(request):  # < 0.13: can't parse a gzip body
        calls.append(request)
        return httpx.Response(422 if "content-encoding" in request.headers else 200)

    s = Shipper(collector_url="http://collector", api_key="k", service_name="svc", sdk_version="t")
    s._client = httpx.Client(base_url="http://collector", transport=httpx.MockTransport(old_collector))
    assert s.send_batch(_big_batch()) is True
    assert [("content-encoding" in c.headers) for c in calls] == [True, False]
    assert s.send_batch(_big_batch()) is True
    assert len(calls) == 3 and "content-encoding" not in calls[2].headers
    assert s.dropped_batches == 0 and s.shipped_events == 40


def test_a_real_422_keeps_gzip_on():
    s, calls = _shipper_with_responses([422])
    assert s.send_batch(_big_batch()) is False
    assert len(calls) == 2  # gzip, then plain: both rejected
    assert s._gzip is True and s.dropped_batches == 1
