"""Projects and per-project keys, end to end through the app (skipped when
TimescaleDB is unreachable)."""

import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import pool as pool_module
from app.main import app
from app.projects import store
from app.users import accounts
from tests.test_queries_db import _db_reachable

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

ENV = {"X-Reqly-Key": settings.ingest_key}
PASSWORD = "correct horse battery"


def _key(k):
    return {"X-Reqly-Key": k}


def _batch(service):
    return {"service_name": service, "events": [{
        "event_id": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": "GET", "route": "/a", "status_code": 200, "duration_ms": 5.0, "error": False,
    }]}


def _otlp(service):
    nanos = str(int(datetime.now(timezone.utc).timestamp() * 1e9))
    return json.dumps({"resourceSpans": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
        "scopeSpans": [{"spans": [{
            "traceId": "0" * 31 + "1", "spanId": uuid.uuid4().hex[:16], "name": "GET /a", "kind": 2,
            "startTimeUnixNano": nanos, "endTimeUnixNano": nanos,
            "attributes": [
                {"key": "http.request.method", "value": {"stringValue": "GET"}},
                {"key": "http.route", "value": {"stringValue": "/a"}},
                {"key": "http.response.status_code", "value": {"intValue": "200"}},
            ],
        }]}],
    }]}).encode()


def test_projects_keys_and_scoping():
    tag = uuid.uuid4().hex[:6]
    alpha_svc, beta_svc, legacy_svc = f"svc-a-{tag}", f"svc-b-{tag}", f"svc-legacy-{tag}"
    member, outsider = f"user-m-{tag}", f"user-o-{tag}"

    with TestClient(app) as client:
        pool = pool_module.get_pool()
        call = client.portal.call
        try:
            # --- projects and keys (env ingest key = admin of every project) -------
            alpha = client.post("/v1/projects", headers=ENV, json={"slug": f"alpha-{tag}", "name": "Alpha"}).json()
            beta = client.post("/v1/projects", headers=ENV, json={"slug": f"beta-{tag}"}).json()
            assert client.post("/v1/projects", headers=ENV, json={"slug": "Bad Slug!"}).status_code == 400

            def new_key(project, scopes):
                r = client.post(f"/v1/projects/{project['id']}/keys", headers=ENV, json={"name": "k", "scopes": scopes})
                assert r.status_code == 200, r.text
                return r.json()

            alpha_rw = new_key(alpha, ["ingest", "read"])
            alpha_ingest_only = new_key(alpha, ["ingest"])
            alpha_admin = new_key(alpha, ["admin"])
            beta_rw = new_key(beta, ["ingest", "read"])
            assert alpha_rw["key"].startswith("rqk_") and alpha_rw["prefix"] == alpha_rw["key"][:12]
            stored = call(pool.fetchval, "SELECT key_hash FROM api_keys WHERE id = $1", alpha_rw["id"])
            assert alpha_rw["key"] not in stored  # only the hash is kept
            assert client.post(f"/v1/projects/{alpha['id']}/keys", headers=ENV,
                               json={"name": "k", "scopes": ["root"]}).status_code == 400

            # --- ingest: a new service joins the writer's project -------------------
            assert client.post("/v1/ingest", headers=_key(alpha_rw["key"]), json=_batch(alpha_svc)).status_code == 200
            assert client.post("/v1/ingest", headers=_key(beta_rw["key"]), json=_batch(beta_svc)).status_code == 200
            assert client.post("/v1/ingest", headers=ENV, json=_batch(legacy_svc)).status_code == 200
            # another project's service is off limits
            r = client.post("/v1/ingest", headers=_key(beta_rw["key"]), json=_batch(alpha_svc))
            assert r.status_code == 403 and "another project" in r.json()["detail"]
            # OTLP: spans of a foreign service are rejected, the rest accepted
            r = client.post("/otlp/v1/traces", headers={**_key(beta_rw["key"]), "Content-Type": "application/json"},
                            content=_otlp(alpha_svc))
            assert r.json()["partialSuccess"]["rejectedSpans"] == "1"
            default_id = call(store.default_project_id, pool)
            assert call(store.project_of, pool, legacy_svc) == default_id
            assert call(store.project_of, pool, alpha_svc) == alpha["id"]
            call(pool.execute, "CALL refresh_continuous_aggregate('route_latency_1min', NULL, NULL)")

            # --- reading is scoped to the key's project ------------------------------
            seen = client.get("/v1/services", headers=_key(alpha_rw["key"])).json()["services"]
            assert alpha_svc in seen and beta_svc not in seen and legacy_svc not in seen
            everything = client.get("/v1/services", headers=_key(settings.read_key)).json()["services"]
            assert {alpha_svc, beta_svc, legacy_svc} <= set(everything)  # public read key: all projects
            summary = "/v1/metrics/summary?window=1h&service_name="
            assert client.get(summary + alpha_svc, headers=_key(alpha_rw["key"])).status_code == 200
            assert client.get(summary + beta_svc, headers=_key(alpha_rw["key"])).status_code == 403
            assert client.get(f"/v1/services/{beta_svc}/releases", headers=_key(alpha_rw["key"])).status_code == 403
            assert client.get(summary + alpha_svc, headers=_key(alpha_ingest_only["key"])).status_code == 403
            projects_seen = client.get("/v1/projects", headers=_key(alpha_rw["key"])).json()["projects"]
            assert [p["id"] for p in projects_seen] == [alpha["id"]] and projects_seen[0]["services"] == [alpha_svc]

            # --- a project admin key manages its own project only ---------------------
            admin_h = _key(alpha_admin["key"])
            assert client.get(f"/v1/projects/{alpha['id']}/keys", headers=admin_h).status_code == 200
            assert client.get(f"/v1/projects/{beta['id']}/keys", headers=admin_h).status_code == 403
            assert client.post("/v1/projects", headers=admin_h, json={"slug": f"gamma-{tag}"}).status_code == 403
            slo = {"service_name": beta_svc, "name": "n", "objective": "availability", "target": 0.99}
            assert client.put("/v1/slos", headers=admin_h, json=slo).status_code == 403
            assert client.put("/v1/slos", headers=admin_h, json={**slo, "service_name": alpha_svc}).status_code == 200

            # --- users: members see their projects; admins see all ---------------------
            call(accounts.create_user, pool, member, PASSWORD)
            call(accounts.create_user, pool, outsider, PASSWORD)
            r = client.post(f"/v1/projects/{alpha['id']}/members", headers=ENV, json={"username": member})
            assert r.status_code == 200

            def bearer(username):
                token = client.post("/v1/auth/login", json={"username": username, "password": PASSWORD}).json()["token"]
                return {"Authorization": f"Bearer {token}"}

            member_h, outsider_h = bearer(member), bearer(outsider)
            assert client.get("/v1/services", headers=member_h).json()["services"] == [alpha_svc]
            assert client.get("/v1/services", headers=outsider_h).json()["services"] == []
            assert client.get(summary + alpha_svc, headers=outsider_h).status_code == 403
            assert client.post(f"/v1/projects/{alpha['id']}/keys", headers=member_h,
                               json={"name": "k", "scopes": ["read"]}).status_code == 403

            # --- moving a service, revoking a key -------------------------------------
            r = client.put(f"/v1/projects/{beta['id']}/services", headers=ENV, json={"service_name": legacy_svc})
            assert r.status_code == 200
            assert legacy_svc in client.get("/v1/services", headers=_key(beta_rw["key"])).json()["services"]

            assert client.post(f"/v1/keys/{alpha_rw['id']}/revoke", headers=admin_h).status_code == 200
            assert client.get("/v1/services", headers=_key(alpha_rw["key"])).status_code == 401
            assert client.post(f"/v1/keys/{alpha_rw['id']}/revoke", headers=admin_h).status_code == 409
            assert client.post(f"/v1/keys/{beta_rw['id']}/revoke", headers=admin_h).status_code == 403
            listed = client.get(f"/v1/projects/{alpha['id']}/keys", headers=ENV).json()["keys"]
            assert all("key" not in k and "key_hash" not in k for k in listed)
            assert [k["revoked_at"] is not None for k in listed if k["id"] == alpha_rw["id"]] == [True]
        finally:
            services = [alpha_svc, beta_svc, legacy_svc]
            call(pool.execute, "DELETE FROM request_events WHERE service_name = ANY($1::text[])", services)
            call(pool.execute, "DELETE FROM slos WHERE service_name = ANY($1::text[])", services)
            call(pool.execute, "DELETE FROM project_services WHERE service_name = ANY($1::text[])", services)
            call(pool.execute, "DELETE FROM projects WHERE slug LIKE $1", f"%-{tag}")
            call(pool.execute, "DELETE FROM users WHERE username LIKE $1", f"user-%-{tag}")
            store.clear_caches()
