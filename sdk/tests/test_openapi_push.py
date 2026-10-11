import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import reqly


class _SpecHandler(BaseHTTPRequestHandler):
    uploads: list = []

    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _SpecHandler.uploads.append((self.path, self.headers.get("X-Reqly-Key"), json.loads(body)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"operations": 2}')

    def do_POST(self):  # event batches
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(202)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        pass


@pytest.fixture
def spec_collector():
    _SpecHandler.uploads = []
    server = HTTPServer(("127.0.0.1", 0), _SpecHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", _SpecHandler.uploads
    finally:
        server.shutdown()
        server.server_close()


def _wait_for(uploads, n=1, timeout=3.0):
    deadline = time.time() + timeout
    while len(uploads) < n and time.time() < deadline:
        time.sleep(0.02)


def test_fastapi_spec_is_uploaded_once_on_first_request(spec_collector):
    url, uploads = spec_collector
    app = FastAPI(title="Shop", version="3.1")
    client = reqly.instrument(
        app, service_name="shop", collector_url=url, api_key="k", push_openapi=True,
        flush_interval_seconds=999,
    )

    # routes registered after instrument() are in the uploaded spec
    @app.get("/users/{user_id}")
    def get_user(user_id: int):
        return {"id": user_id}

    @app.get("/old", deprecated=True)
    def old():
        return {}

    time.sleep(0.2)
    assert uploads == []  # nothing before the first request

    http = TestClient(app)
    for _ in range(3):
        assert http.get("/users/1").status_code == 200
    _wait_for(uploads)
    time.sleep(0.2)

    assert len(uploads) == 1
    path, key, spec = uploads[0]
    assert path == "/v1/services/shop/openapi" and key == "k"
    assert spec["info"] == {"title": "Shop", "version": "3.1"}
    assert set(spec["paths"]) == {"/users/{user_id}", "/old"}
    assert spec["paths"]["/old"]["get"]["deprecated"] is True
    client.shutdown()


def test_push_is_off_by_default(spec_collector):
    url, uploads = spec_collector
    app = FastAPI()

    @app.get("/x")
    def x():
        return {}

    client = reqly.instrument(app, service_name="shop", collector_url=url, flush_interval_seconds=999)
    TestClient(app).get("/x")
    time.sleep(0.3)
    assert uploads == []
    client.shutdown()


def test_env_var_turns_it_on(spec_collector, monkeypatch):
    url, uploads = spec_collector
    monkeypatch.setenv("REQLY_PUSH_OPENAPI", "true")
    app = FastAPI()

    @app.get("/x")
    def x():
        return {}

    client = reqly.instrument(app, service_name="shop", collector_url=url, flush_interval_seconds=999)
    TestClient(app).get("/x")
    _wait_for(uploads)
    assert len(uploads) == 1
    client.shutdown()


def test_upload_failures_never_reach_the_app(caplog):
    app = FastAPI()

    @app.get("/x")
    def x():
        return {"ok": True}

    def broken_openapi():
        raise RuntimeError("schema generation failed")

    app.openapi = broken_openapi
    client = reqly.instrument(
        app, service_name="shop", collector_url="http://127.0.0.1:1", push_openapi=True,
        flush_interval_seconds=999,
    )
    with caplog.at_level(logging.WARNING, logger="reqly"):
        assert TestClient(app).get("/x").json() == {"ok": True}
        time.sleep(0.3)
    assert "OpenAPI spec upload failed" in caplog.text
    assert client.stats()["disabled"] is False
    client.shutdown()


def test_litestar_spec_is_uploaded(spec_collector):
    litestar = pytest.importorskip("litestar")
    from litestar.testing import TestClient as LitestarClient

    url, uploads = spec_collector

    @litestar.get("/items/{item_id:int}")
    async def get_item(item_id: int) -> dict:
        return {"id": item_id}

    app = litestar.Litestar([get_item])
    client = reqly.instrument(
        app, service_name="items", collector_url=url, push_openapi=True, flush_interval_seconds=999,
    )
    with LitestarClient(app) as http:
        assert http.get("/items/3").status_code == 200
    _wait_for(uploads)
    assert len(uploads) == 1
    assert "/items/{item_id}" in uploads[0][2]["paths"]
    client.shutdown()


def test_flask_warns_instead(caplog):
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)
    with caplog.at_level(logging.WARNING, logger="reqly"):
        client = reqly.instrument(app, service_name="f", collector_url="http://127.0.0.1:1", push_openapi=True)
    assert "push_openapi=True needs an app that generates its own spec" in caplog.text
    assert "push_openapi=spec_dict" in caplog.text  # says how to pass it
    assert client is not None
    client.shutdown()


def test_service_name_is_escaped_in_the_upload_url():
    from reqly.core.openapi_push import OpenAPIPusher

    pusher = OpenAPIPusher(spec_factory=dict, collector_url="http://c/", api_key=None, service_name="team a/api")
    assert pusher._url == "http://c/v1/services/team%20a%2Fapi/openapi"


SPEC = {"openapi": "3.0.0", "info": {"title": "Flask shop", "version": "1"},
        "paths": {"/orders/{id}": {"get": {}}}}


def test_flask_uploads_a_spec_dict(spec_collector):
    from flask import Flask

    url, uploads = spec_collector
    app = Flask(__name__)

    @app.get("/orders/<int:id>")
    def order(id):
        return {"id": id}

    client = reqly.instrument(app, service_name="flask-shop", collector_url=url, push_openapi=SPEC,
                              flush_interval_seconds=999)
    app.test_client().get("/orders/1")
    _wait_for(uploads)
    assert uploads[0][0] == "/v1/services/flask-shop/openapi" and uploads[0][2] == SPEC
    client.shutdown()


def test_wsgi_uploads_the_spec_a_function_returns(spec_collector):
    url, uploads = spec_collector

    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    wrapped = reqly.instrument_wsgi(app, service_name="wsgi-shop", collector_url=url,
                                    push_openapi=lambda: SPEC, flush_interval_seconds=999)
    body = wrapped({"REQUEST_METHOD": "GET", "PATH_INFO": "/"}, lambda *a: None)
    list(body)
    body.close()
    _wait_for(uploads)
    assert uploads[0][2] == SPEC
    wrapped.client.shutdown()


def test_push_openapi_true_without_a_spec_source_warns(caplog):
    caplog.set_level(logging.WARNING, logger="reqly")

    def app(environ, start_response):
        return []

    wrapped = reqly.instrument_wsgi(app, service_name="s", collector_url="http://127.0.0.1:1",
                                    push_openapi=True, flush_interval_seconds=999)
    assert "push_openapi=True needs the spec" in caplog.text
    wrapped.client.shutdown()


def test_capture_request_body_is_ignored_with_a_warning(caplog):
    caplog.set_level(logging.WARNING, logger="reqly")

    def app(environ, start_response):
        return []

    wrapped = reqly.instrument_wsgi(app, service_name="s", collector_url="http://127.0.0.1:1",
                                    capture_request_body=True, flush_interval_seconds=999)
    assert "capture_request_body is ignored" in caplog.text
    wrapped.client.shutdown()
