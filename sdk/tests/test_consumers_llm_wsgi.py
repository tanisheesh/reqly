"""Consumers, LLM usage and the generic WSGI/ASGI wrappers (SDK 0.5.0)."""

import hashlib
import hmac
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import reqly
from reqly.core.request_context import LLMUsage


def _events(client, batches):
    client._buffer.flush()
    time.sleep(0.1)
    return [e for b in batches for e in b["events"]]


def _hash(value, salt="s3cret"):
    return hmac.new(salt.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]


# --- consumers -----------------------------------------------------------------

def _fastapi_app():
    app = FastAPI()

    @app.get("/items/{item_id}")
    def item(item_id: int):
        return {"id": item_id}

    return app


def test_consumer_header_is_hashed_with_the_salt(fake_collector):
    url, batches = fake_collector
    app = _fastapi_app()
    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999,
                              consumer_header="X-API-Key", consumer_salt="s3cret")
    http = TestClient(app)
    http.get("/items/1", headers={"X-API-Key": "key_live_abc"})
    http.get("/items/2")  # no header: no consumer
    events = _events(client, batches)
    assert [e["consumer_id"] for e in events] == [_hash("key_live_abc"), None]
    assert "key_live_abc" not in str(events)
    client.shutdown()


def test_consumer_callable_and_unhashed_ids(fake_collector):
    url, batches = fake_collector
    app = _fastapi_app()

    def tenant(info):
        assert info.method == "GET" and info.path.startswith("/items/")
        assert info.raw["type"] == "http"  # the ASGI scope
        return info.headers.get("x-tenant")

    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999,
                              consumer=tenant, hash_consumer=False)
    TestClient(app).get("/items/1", headers={"X-Tenant": "acme"})
    assert [e["consumer_id"] for e in _events(client, batches)] == ["acme"]
    client.shutdown()


def test_failing_consumer_callable_costs_only_the_consumer(fake_collector):
    url, batches = fake_collector
    app = _fastapi_app()

    def broken(info):
        raise KeyError("tenant")

    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999,
                              consumer=broken, consumer_salt="x")
    assert TestClient(app).get("/items/1").status_code == 200
    events = _events(client, batches)
    assert len(events) == 1 and events[0]["consumer_id"] is None
    assert client.stats()["disabled"] is False
    client.shutdown()


def test_consumer_env_vars(fake_collector, monkeypatch):
    url, batches = fake_collector
    monkeypatch.setenv("REQLY_CONSUMER_HEADER", "Authorization")
    monkeypatch.setenv("REQLY_CONSUMER_SALT", "s3cret")
    app = _fastapi_app()
    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999)
    TestClient(app).get("/items/1", headers={"Authorization": "Bearer t0k"})
    assert [e["consumer_id"] for e in _events(client, batches)] == [_hash("Bearer t0k")]
    client.shutdown()


# --- LLM usage -----------------------------------------------------------------

def test_llm_usage_from_async_and_sync_endpoints(fake_collector):
    url, batches = fake_collector
    app = FastAPI()

    @app.post("/chat")
    async def chat():
        reqly.record_llm_usage("gpt-4o-mini", input_tokens=120, output_tokens=30)
        reqly.record_llm_usage("gpt-4o-mini", 80, 20)  # calls add up
        return {}

    @app.post("/summarize")
    def summarize():  # runs in a thread pool with a copied context
        reqly.record_llm_response({"model": "claude-haiku", "usage": {"input_tokens": 900, "output_tokens": 100}})
        return {}

    @app.get("/plain")
    def plain():
        return {}

    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999)
    http = TestClient(app)
    http.post("/chat")
    http.post("/summarize")
    http.get("/plain")
    events = {e["route"]: e for e in _events(client, batches)}
    assert (events["/chat"]["llm_model"], events["/chat"]["llm_input_tokens"], events["/chat"]["llm_output_tokens"]) == ("gpt-4o-mini", 200, 50)
    assert (events["/summarize"]["llm_model"], events["/summarize"]["llm_input_tokens"]) == ("claude-haiku", 900)
    assert events["/plain"]["llm_model"] is None and events["/plain"]["llm_input_tokens"] is None
    client.shutdown()


def test_record_llm_response_shapes():
    class Usage:
        prompt_tokens = 11
        completion_tokens = 7

    class OpenAIResponse:
        model = "gpt-4.1"
        usage = Usage()

    from reqly.core import request_context as rc

    token = rc.begin_request()
    reqly.record_llm_response(OpenAIResponse())
    reqly.record_llm_response({"model": "claude-sonnet", "usage": {"input_tokens": 1000, "output_tokens": 500}})
    reqly.record_llm_response({"model": "x"})  # no usage: ignored
    reqly.record_llm_response(None)  # never raises
    reqly.record_llm_usage("bad", -5, 0)  # rejected, logged
    # several models: attributed to the biggest, with all tokens summed
    assert rc.end_request(token) == ("claude-sonnet", 1011, 507)


def test_record_llm_usage_outside_a_request_is_a_no_op():
    reqly.record_llm_usage("gpt-4o", 10, 10)  # nothing to attach to, no error
    assert LLMUsage().summary() is None


# --- Flask / Django ------------------------------------------------------------

def test_flask_consumer_and_llm(fake_collector):
    flask = pytest.importorskip("flask")
    url, batches = fake_collector
    app = flask.Flask(__name__)

    @app.post("/ask")
    def ask():
        reqly.record_llm_usage("gpt-4o", 50, 10)
        return "ok"

    client = reqly.instrument(app, service_name="f", collector_url=url, flush_interval_seconds=999,
                              consumer_header="X-API-Key", consumer_salt="s3cret")
    app.test_client().post("/ask", headers={"X-API-Key": "k1"})
    [event] = _events(client, batches)
    assert event["consumer_id"] == _hash("k1")
    assert (event["llm_model"], event["llm_input_tokens"], event["llm_output_tokens"]) == ("gpt-4o", 50, 10)
    client.shutdown()


# --- generic WSGI / ASGI -------------------------------------------------------

def _wsgi_app(environ, start_response):
    path = environ["PATH_INFO"]
    if path == "/boom":
        raise RuntimeError("boom")
    if path.startswith("/users/"):
        environ["myframework.route"] = "/users/{id}"
        reqly.record_llm_usage("gpt-4o-mini", 5, 1)
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"hello ", b"world"]  # streamed in two chunks
    start_response("404 Not Found", [])
    return [b"nope"]


def test_instrument_wsgi(fake_collector):
    from werkzeug.test import Client

    url, batches = fake_collector
    app = reqly.instrument_wsgi(
        _wsgi_app, service_name="w", collector_url=url, flush_interval_seconds=999,
        route_resolver=lambda environ: environ.get("myframework.route"),
        consumer_header="X-API-Key", consumer_salt="s3cret",
    )
    http = Client(app)
    assert http.get("/users/42", headers={"X-API-Key": "k"}).data == b"hello world"
    missing = http.get("/missing")
    assert missing.status_code == 404 and missing.data == b"nope"  # body read: the response is closed
    with pytest.raises(RuntimeError):
        http.get("/boom")

    events = _events(app.client, batches)
    by_status = {e["status_code"]: e for e in events}
    ok = by_status[200]
    assert ok["route"] == "/users/{id}" and ok["response_bytes"] == 11
    assert ok["consumer_id"] == _hash("k") and ok["llm_model"] == "gpt-4o-mini"
    assert by_status[404]["route"] == "__unmatched__" and by_status[404]["error"] is False
    boom = by_status[500]
    assert boom["error"] is True and boom["error_type"] == "RuntimeError"
    assert len(events) == 3
    app.client.shutdown()


def test_instrument_wsgi_without_resolver_warns(caplog):
    app = reqly.instrument_wsgi(_wsgi_app, service_name="w", collector_url="http://127.0.0.1:1", bogus=1)
    assert "without route_resolver" in caplog.text and "unknown options ['bogus']" in caplog.text
    app.client.shutdown()


def test_instrument_asgi(fake_collector):
    url, batches = fake_collector

    async def raw_asgi(scope, receive, send):
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"created"})

    app = reqly.instrument_asgi(
        raw_asgi, service_name="a", collector_url=url, flush_interval_seconds=999,
        route_resolver=lambda scope: "/things" if scope["path"] == "/things" else None,
    )
    from starlette.testclient import TestClient as ASGIClient

    ASGIClient(app).post("/things")
    [event] = _events(app.client, batches)
    assert (event["route"], event["status_code"], event["response_bytes"]) == ("/things", 201, 7)
    app.client.shutdown()
