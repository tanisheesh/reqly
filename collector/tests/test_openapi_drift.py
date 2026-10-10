"""OpenAPI drift: path matching and the report (pure), plus the upload
endpoint's parsing and auth (no database)."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.openapi import drift
from app.openapi.drift import Traffic, compare, match_operation, normalize_path, spec_operations
from app.routers import openapi as openapi_router

T = datetime(2026, 10, 10, tzinfo=timezone.utc)

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "Shop", "version": "2.0"},
    "paths": {
        "/users/{user_id}": {"get": {"operationId": "getUser"}, "delete": {"operationId": "deleteUser"}},
        "/users/me": {"get": {"operationId": "me"}},
        "/products": {"get": {"deprecated": True, "summary": "List products (use /catalog)"}},
        "/orders/": {"post": {}, "parameters": []},
        "/": {"get": {}},
    },
}


@pytest.mark.parametrize("path, shape", [
    ("/users/{user_id}", "/users/{}"),
    ("/users/{id:path}", "/users/{}"),
    ("/users/:id", "/users/{}"),
    ("/users/<int:id>", "/users/{}"),
    ("/users/<id>/orders/", "/users/{}/orders"),
    ("users//me/", "/users/me"),
    ("/", "/"),
])
def test_normalize_path(path, shape):
    assert normalize_path(path) == shape


def test_operations_from_spec():
    ops = spec_operations(SPEC)
    assert sorted((o.method, o.path) for o in ops) == [
        ("DELETE", "/users/{user_id}"), ("GET", "/"), ("GET", "/products"), ("GET", "/users/me"),
        ("GET", "/users/{user_id}"), ("POST", "/orders/"),
    ]  # the path-level "parameters" key is not an operation
    assert [o.path for o in spec_operations(SPEC, "/api/")][0] == "/api/users/{user_id}"


def test_matching_across_frameworks_and_literal_precedence():
    ops = spec_operations(SPEC)
    assert match_operation("GET", "/users/<int:id>", ops).operation_id == "getUser"
    assert match_operation("GET", "/users/:id", ops).operation_id == "getUser"
    assert match_operation("GET", "/users/42", ops).operation_id == "getUser"  # un-templated
    assert match_operation("GET", "/users/me", ops).operation_id == "me"  # literal wins
    assert match_operation("DELETE", "/users/{id}", ops).operation_id == "deleteUser"
    assert match_operation("POST", "/orders", ops).path == "/orders/"  # trailing slash
    assert match_operation("PUT", "/users/{id}", ops) is None  # method matters
    assert match_operation("GET", "/users/{id}/orders", ops) is None


def _t(method, route, requests, errors=0):
    return Traffic(method=method, route=route, requests=requests, errors=errors, last_seen=T)


def test_compare_finds_undocumented_dead_and_deprecated():
    report = compare(spec_operations(SPEC), [
        _t("GET", "/users/{id}", 500, 5),
        _t("GET", "/users/me", 80),
        _t("GET", "/products", 120),
        _t("POST", "/orders", 300, 9),
        _t("GET", "/internal/debug", 40, 4),   # undocumented
        _t("PATCH", "/users/{id}", 7),         # undocumented: method not in spec
        _t("HEAD", "/health", 50),             # implicit method: ignored
        _t("GET", drift.UNMATCHED_ROUTE, 30),  # 404 traffic
    ]).to_dict()

    assert [(u["method"], u["route"]) for u in report["undocumented"]] == [
        ("GET", "/internal/debug"), ("PATCH", "/users/{id}"),
    ]
    assert report["undocumented"][0]["error_rate"] == 0.1
    assert report["undocumented_requests"] == 47
    assert [(d["method"], d["path"]) for d in report["dead"]] == [("GET", "/"), ("DELETE", "/users/{user_id}")]
    assert report["deprecated_in_use"] == [{
        "method": "GET", "path": "/products", "summary": "List products (use /catalog)",
        "deprecated": True, "requests": 120, "last_seen": T, "routes": ["/products"],
    }]
    assert report["operations"] == 6 and report["documented_in_use"] == 4
    assert report["coverage"] == pytest.approx(4 / 6, abs=1e-4)
    assert report["unmatched_requests"] == 30 and report["total_requests"] == 1127


# --- upload endpoint -----------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    saved = {}

    async def save_spec(pool, service, spec, base_path):
        saved.update(service=service, spec=spec, base_path=base_path)
        return {"uploaded_at": T.isoformat()}

    monkeypatch.setattr(openapi_router, "get_pool", lambda: None)
    monkeypatch.setattr(openapi_router.store, "save_spec", save_spec)
    client = TestClient(app)
    client.saved = saved
    return client


def _put(api, body, content_type="application/json", key=None, query=""):
    return api.put(
        f"/v1/services/shop/openapi{query}", content=body,
        headers={"Content-Type": content_type, "X-Reqly-Key": key or settings.ingest_key},
    )


def test_upload_json_and_yaml(api):
    import json

    response = _put(api, json.dumps(SPEC))
    assert response.status_code == 200, response.text
    assert response.json()["operations"] == 6 and api.saved["base_path"] == ""

    yaml_spec = "swagger: '2.0'\nbasePath: /v1\npaths:\n  /pets/{id}:\n    get: {}\n"
    response = _put(api, yaml_spec, content_type="application/yaml")
    assert response.status_code == 200, response.text
    assert api.saved["base_path"] == "/v1"  # Swagger 2.0 basePath
    response = _put(api, yaml_spec, content_type="text/plain", query="?base_path=/api")
    assert api.saved["base_path"] == "/api"  # explicit wins; YAML sniffed without the content type


@pytest.mark.parametrize("body, error", [
    ('{"paths": {}}', "not an OpenAPI document"),
    ('{"openapi": "3.0.0"}', 'no "paths"'),
    ("{not json", "invalid JSON"),
    ("openapi: [unclosed", "invalid YAML"),
])
def test_upload_rejects_non_specs(api, body, error):
    response = _put(api, body)
    assert response.status_code == 422 and error in response.json()["detail"]


def test_upload_needs_the_ingest_key(api):
    response = _put(api, '{"openapi": "3.0.0", "paths": {}}', key=settings.read_key)
    assert response.status_code == 401
