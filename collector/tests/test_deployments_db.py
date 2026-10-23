"""POST /v1/deployments: deploy markers before (and after) traffic
(skipped when TimescaleDB is unreachable)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import pool as pool_module
from app.main import app
from tests.test_queries_db import _db_reachable

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

INGEST = {"X-Reqly-Key": settings.ingest_key}
READ = {"X-Reqly-Key": settings.read_key}


def test_markers_set_the_release_start_and_widen_it():
    service = f"test-deploy-{uuid.uuid4().hex[:8]}"
    deployed = datetime.now(timezone.utc) - timedelta(hours=3)
    with TestClient(app) as client:
        pool = pool_module.get_pool()
        try:
            r = client.post("/v1/deployments", headers=INGEST, json={
                "service_name": service, "release": "v42", "environment": "production",
                "deployed_at": deployed.isoformat()})
            assert r.status_code == 200, r.text
            assert (r.json()["release"], r.json()["environment"]) == ("v42", "production")

            # an earlier marker moves the start back; a later one only extends last_seen
            earlier = deployed - timedelta(hours=1)
            client.post("/v1/deployments", headers=INGEST, json={
                "service_name": service, "release": "v42", "environment": "production", "deployed_at": earlier.isoformat()})
            last = client.post("/v1/deployments", headers=INGEST, json={
                "service_name": service, "release": "v42", "environment": "production"}).json()
            assert datetime.fromisoformat(last["first_seen_at"]) == earlier
            assert datetime.fromisoformat(last["last_seen_at"]) > deployed

            releases = client.get(f"/v1/services/{service}/releases", headers=READ).json()["releases"]
            assert [r["release"] for r in releases] == ["v42"]

            # the read key can't write; bad times are refused; the service is claimed for the writer
            assert client.post("/v1/deployments", headers=READ, json={"service_name": service, "release": "v43"}).status_code == 401  # like /v1/ingest
            future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
            assert client.post("/v1/deployments", headers=INGEST, json={
                "service_name": service, "release": "v43", "deployed_at": future}).status_code == 422
            assert client.post("/v1/deployments", headers=INGEST, json={"service_name": "", "release": "x"}).status_code == 422
            assert client.portal.call(pool.fetchval, "SELECT count(*) FROM project_services WHERE service_name = $1", service) == 1
        finally:
            client.portal.call(pool.execute, "DELETE FROM deployments WHERE service_name = $1", service)
            client.portal.call(pool.execute, "DELETE FROM project_services WHERE service_name = $1", service)
