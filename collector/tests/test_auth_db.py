"""Users, sessions and endpoint access against TimescaleDB (skipped when
unreachable)."""

import asyncio
import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_module
from app.config import settings
from app.db import pool as pool_module
from app.main import app
from app.routers import alerts as alerts_router
from app.users import accounts
from tests.test_queries_db import _db_reachable

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")

PASSWORD = "correct horse battery"


def _name():
    return f"user-{uuid.uuid4().hex[:8]}"


def _with_pool(fn):
    async def run():
        pool = await pool_module.create_pool()
        try:
            return await fn(pool)
        finally:
            await pool.execute("DELETE FROM users WHERE username LIKE 'user-%' OR username LIKE 'admin-%'")
            await pool_module.close_pool()

    return asyncio.run(run())


def test_create_login_session_logout():
    name = _name()

    async def scenario(pool):
        user = await accounts.create_user(pool, name.upper(), PASSWORD)  # stored lower-case
        with pytest.raises(accounts.AuthError, match="already exists"):
            await accounts.create_user(pool, name, PASSWORD)
        with pytest.raises(accounts.AuthError, match="at least 12"):
            await accounts.create_user(pool, _name(), "short")

        for username, password in ((name, "wrong password!"), ("nobody-" + name, PASSWORD), ("", PASSWORD)):
            with pytest.raises(accounts.AuthError, match="invalid username or password"):
                await accounts.login(pool, username, password, timedelta(hours=1))

        token, expires_at, logged_in = await accounts.login(pool, f"  {name.upper()} ", PASSWORD, timedelta(hours=1))
        assert logged_in == user and not user["is_admin"]
        stored = await pool.fetchval("SELECT token_hash FROM sessions WHERE user_id = $1", user["id"])
        assert stored != token and len(stored) == 64  # only a hash is kept
        assert await accounts.session_user(pool, token) == user
        assert await accounts.session_user(pool, token, now=expires_at + timedelta(seconds=1)) is None
        assert await accounts.session_user(pool, "made-up") is None

        await accounts.logout(pool, token)
        assert await accounts.session_user(pool, token) is None
        return True

    assert _with_pool(scenario)


def test_password_changes_end_sessions():
    name = _name()

    async def scenario(pool):
        user = await accounts.create_user(pool, name, PASSWORD)
        token, _, _ = await accounts.login(pool, name, PASSWORD, timedelta(hours=1))
        with pytest.raises(accounts.AuthError, match="current password is wrong"):
            await accounts.change_password(pool, user["id"], "not it at all", "a brand new password")
        await accounts.change_password(pool, user["id"], PASSWORD, "a brand new password")
        assert await accounts.session_user(pool, token) is None
        await accounts.login(pool, name, "a brand new password", timedelta(hours=1))

        token, _, _ = await accounts.login(pool, name, "a brand new password", timedelta(hours=1))
        await accounts.set_password(pool, name, "reset by an operator")  # the CLI path
        assert await accounts.session_user(pool, token) is None
        await accounts.login(pool, name, "reset by an operator", timedelta(hours=1))
        return True

    assert _with_pool(scenario)


def test_bootstrap_admin_only_when_there_are_no_users():
    async def scenario(pool):
        # users from other tests may exist; simulate an empty table in a transaction
        async with pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            try:
                await conn.execute("DELETE FROM users")
                fake_pool = _ConnPool(conn)
                assert await accounts.bootstrap_admin(fake_pool, "admin-x", None) is None  # no password: nothing
                admin = await accounts.bootstrap_admin(fake_pool, "admin-x", PASSWORD)
                assert admin["is_admin"] and admin["username"] == "admin-x"
                assert await accounts.bootstrap_admin(fake_pool, "admin-y", PASSWORD) is None  # users exist
            finally:
                await tx.rollback()
        return True

    assert _with_pool(scenario)


class _ConnPool:
    """Just enough of a pool, bound to one connection (inside a transaction)."""

    def __init__(self, conn):
        self.conn = conn

    async def fetchval(self, *a):
        return await self.conn.fetchval(*a)

    async def fetchrow(self, *a):
        return await self.conn.fetchrow(*a)


@pytest.fixture
def private_dashboard(monkeypatch):
    monkeypatch.setattr(auth_module, "settings", dataclasses.replace(settings, public_dashboard=False))


def test_endpoint_access(private_dashboard, monkeypatch):
    """Through the real app (its own lifespan, pool and event loop)."""
    member, admin = _name(), "admin-" + uuid.uuid4().hex[:6]
    monkeypatch.setattr(alerts_router, "get_pool", lambda: _Empty())
    read_key = {"X-Reqly-Key": settings.read_key}

    with TestClient(app) as client:
        pool = pool_module.get_pool()
        try:
            client.portal.call(accounts.create_user, pool, member, PASSWORD)
            client.portal.call(lambda: accounts.create_user(pool, admin, PASSWORD, is_admin=True))

            assert client.post("/v1/auth/login", json={"username": member, "password": "nope nope nope"}).status_code == 401
            login = client.post("/v1/auth/login", json={"username": member, "password": PASSWORD}).json()
            assert login["user"]["username"] == member
            member_auth = {"Authorization": f"Bearer {login['token']}"}
            admin_token = client.post("/v1/auth/login", json={"username": admin, "password": PASSWORD}).json()["token"]
            admin_auth = {"Authorization": f"Bearer {admin_token}"}

            r = client.get("/v1/alerts", headers=read_key)
            assert r.status_code == 401 and "requires signing in" in r.json()["detail"]
            assert client.get("/v1/alerts", headers=member_auth).status_code == 200
            assert client.get("/v1/alerts", headers={"Authorization": "Bearer nope"}).status_code == 401
            assert client.get("/v1/auth/me", headers=member_auth).json()["user"]["username"] == member
            assert client.get("/v1/auth/me").status_code == 401

            slo = {"service_name": "s", "name": "n", "objective": "availability", "target": 0.99}
            assert client.put("/v1/slos", headers=member_auth, json=slo).status_code == 403
            # an admin is authorized (then the body is validated)
            assert client.put("/v1/slos", headers=admin_auth, json={"service_name": "s"}).status_code == 422

            client.post("/v1/auth/logout", headers=member_auth)
            assert client.get("/v1/alerts", headers=member_auth).status_code == 401
        finally:
            client.portal.call(pool.execute, "DELETE FROM users WHERE username = ANY($1::text[])", [member, admin])


class _Empty:
    async def fetch(self, *a, **k):
        return []


def test_public_dashboard_still_accepts_the_read_key(monkeypatch):
    monkeypatch.setattr(alerts_router, "get_pool", lambda: _Empty())
    client = TestClient(app)
    assert client.get("/v1/alerts", headers={"X-Reqly-Key": settings.read_key}).status_code == 200
    assert client.get("/v1/alerts", headers={"X-Reqly-Key": settings.ingest_key}).status_code == 401
