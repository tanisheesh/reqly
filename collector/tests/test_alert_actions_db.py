"""Acknowledge, silence and mute: who may, and which notifications they stop
(skipped when TimescaleDB is unreachable)."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.alerts import actions, notifier
from app.config import settings
from app.db import pool as pool_module
from app.main import app
from tests.test_queries_db import _db_reachable

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

ADMIN = {"X-Reqly-Key": settings.ingest_key}
READ = {"X-Reqly-Key": settings.read_key}


def test_on_call_actions():
    service = f"test-oncall-{uuid.uuid4().hex[:8]}"
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    with TestClient(app) as client:
        pool = pool_module.get_pool()
        call = client.portal.call

        async def new_alert(route, kind="anomaly"):
            return await pool.fetchval(
                "INSERT INTO alerts (service_name, route, kind, first_hour, last_hour, details) "
                "VALUES ($1, $2, $3, $4, $4, $5::jsonb) RETURNING id",
                service, route, kind, hour, json.dumps({"route": route}),
            )

        def notifies(alert_id, route, event):
            alert = {"id": alert_id, "service_name": service, "route": route}
            return call(actions.should_notify, pool, event, alert, datetime.now(timezone.utc))

        try:
            orders, users = call(new_alert, "/orders"), call(new_alert, "/users")
            cost = call(new_alert, "/orders", "llm_cost")
            assert notifies(orders, "/orders", notifier.STILL_FIRING)

            # the public read key can't page or un-page anyone
            assert client.post(f"/v1/alerts/{orders}/acknowledge", headers=READ).status_code == 403
            assert client.post("/v1/alert-mutes", headers=READ,
                               json={"service_name": service, "hours": 1}).status_code == 403
            assert client.post("/v1/alerts/999999999/acknowledge", headers=ADMIN).status_code == 404

            # acknowledged: no reminders, but the resolve still goes out
            r = client.post(f"/v1/alerts/{orders}/acknowledge", headers=ADMIN)
            assert r.status_code == 200 and r.json()["acknowledged_by"] == "ingest-key"
            assert not notifies(orders, "/orders", notifier.STILL_FIRING)
            assert notifies(orders, "/orders", notifier.RESOLVED)

            # silenced: nothing until it ends; 0 hours lifts it
            assert client.post(f"/v1/alerts/{users}/silence", headers=ADMIN, json={"hours": 2}).status_code == 200
            assert not notifies(users, "/users", notifier.RESOLVED)
            assert client.post(f"/v1/alerts/{users}/silence", headers=ADMIN, json={"hours": 0}).status_code == 200
            assert notifies(users, "/users", notifier.RESOLVED)
            assert client.post(f"/v1/alerts/{users}/silence", headers=ADMIN, json={"hours": 999}).status_code == 422

            # muted: one route, then the whole service
            r = client.post("/v1/alert-mutes", headers=ADMIN,
                            json={"service_name": service, "route": "/users", "hours": 1, "reason": "deploy"})
            assert r.status_code == 200
            route_mute = r.json()["id"]
            assert not notifies(users, "/users", notifier.OPENED)
            assert notifies(orders, "/orders", notifier.RESOLVED)
            client.post("/v1/alert-mutes", headers=ADMIN, json={"service_name": service, "kind": "llm_cost", "hours": 1})
            assert notifies(orders, "/orders", notifier.RESOLVED)  # another kind
            assert not notifies(cost, "/orders", notifier.OPENED)

            listed = {a["id"]: a for a in client.get(f"/v1/alerts?service_name={service}", headers=READ).json()["alerts"]}
            assert listed[orders]["acknowledged_by"] == "ingest-key" and listed[users]["muted"] is True
            mutes = client.get(f"/v1/alert-mutes?service_name={service}", headers=READ).json()["mutes"]
            assert len(mutes) == 2

            assert client.delete(f"/v1/alert-mutes/{route_mute}", headers=ADMIN).status_code == 200
            assert notifies(users, "/users", notifier.OPENED)

            call(pool.execute, "UPDATE alerts SET resolved_at = now() WHERE id = $1", orders)
            assert client.post(f"/v1/alerts/{orders}/silence", headers=ADMIN, json={"hours": 1}).status_code == 409
        finally:
            call(pool.execute, "DELETE FROM alerts WHERE service_name = $1", service)
            call(pool.execute, "DELETE FROM alert_mutes WHERE service_name = $1", service)


def test_a_muted_alert_is_not_sent_even_with_channels(monkeypatch):
    sent = []

    async def fake_send(channels, event, alert, client=None):
        sent.append(event)
        return 1

    monkeypatch.setattr(actions.notifier, "send", fake_send)
    channels = notifier.Channels(webhook_url="https://example.test/hook")

    async def run():
        pool = await pool_module.create_pool()
        service = f"test-mute-{uuid.uuid4().hex[:8]}"
        try:
            alert_id = await pool.fetchval(
                "INSERT INTO alerts (service_name, route, first_hour, last_hour, details) "
                "VALUES ($1, '/x', now(), now(), '{}'::jsonb) RETURNING id", service,
            )
            alert = {"id": alert_id, "service_name": service, "route": "/x", "kind": "anomaly"}
            first = await actions.deliver(pool, channels, notifier.OPENED, alert)
            await actions.add_mute(pool, service, None, None, datetime.now(timezone.utc) + timedelta(hours=1), None, "t")
            second = await actions.deliver(pool, channels, notifier.STILL_FIRING, alert)
            return first, second
        finally:
            await pool.execute("DELETE FROM alerts WHERE service_name = $1", service)
            await pool.execute("DELETE FROM alert_mutes WHERE service_name = $1", service)
            await pool_module.close_pool()

    import asyncio

    assert asyncio.run(run()) == (1, 0) and sent == [notifier.OPENED]
