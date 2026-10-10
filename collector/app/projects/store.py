"""Projects, service ownership and per-project API keys.

Key lookups and service -> project lookups sit on the ingest hot path, so
both are cached in memory for CACHE_TTL: a revoked key or a moved service
takes effect within that time (and immediately on this instance, which
clears its cache when it makes the change).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg

KEY_PREFIX = "rqk_"
SCOPES = ("ingest", "read", "admin")
CACHE_TTL = 60.0  # seconds
LAST_USED_RESOLUTION = timedelta(minutes=1)
DEFAULT_PROJECT_SLUG = "default"


class ProjectError(Exception):
    """A user-facing validation failure (bad slug, unknown project...)."""


@dataclass(frozen=True)
class ProjectKey:
    id: int
    project_id: int
    scopes: frozenset[str]


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class _TTLCache:
    def __init__(self) -> None:
        self._items: dict = {}

    def get(self, key):
        hit = self._items.get(key)
        if hit is None or hit[1] < time.monotonic():
            return None
        return hit

    def put(self, key, value) -> None:
        if len(self._items) > 10_000:  # bounded; stale entries are cheap to refetch
            self._items.clear()
        self._items[key] = (value, time.monotonic() + CACHE_TTL)

    def clear(self) -> None:
        self._items.clear()


_key_cache = _TTLCache()
_service_cache = _TTLCache()
_last_used: dict[int, datetime] = {}


def clear_caches() -> None:
    _key_cache.clear()
    _service_cache.clear()


# --- projects ------------------------------------------------------------------

def _project(row) -> dict:
    return {"id": row["id"], "slug": row["slug"], "name": row["name"], "created_at": row["created_at"]}


async def create_project(pool: asyncpg.Pool, slug: str, name: str) -> dict:
    slug = (slug or "").strip().lower()
    try:
        row = await pool.fetchrow(
            "INSERT INTO projects (slug, name) VALUES ($1, $2) RETURNING *", slug, (name or slug).strip()[:128]
        )
    except asyncpg.CheckViolationError:
        raise ProjectError("slug must be 1-63 lowercase letters, digits or dashes, starting with a letter or digit")
    except asyncpg.UniqueViolationError:
        raise ProjectError(f"project {slug!r} already exists")
    return _project(row)


async def list_projects(pool: asyncpg.Pool, project_ids: frozenset[int] | None = None) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT p.*, coalesce(array_agg(s.service_name ORDER BY s.service_name)
                             FILTER (WHERE s.service_name IS NOT NULL), '{}') AS services
        FROM projects p LEFT JOIN project_services s ON s.project_id = p.id
        WHERE $1::bigint[] IS NULL OR p.id = ANY($1::bigint[])
        GROUP BY p.id ORDER BY p.id
        """,
        sorted(project_ids) if project_ids is not None else None,
    )
    return [{**_project(r), "services": list(r["services"])} for r in rows]


async def get_project(pool: asyncpg.Pool, project_id: int) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM projects WHERE id = $1", project_id)
    return _project(row) if row else None


async def default_project_id(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("SELECT id FROM projects WHERE slug = $1", DEFAULT_PROJECT_SLUG)


# --- services --------------------------------------------------------------------

async def project_of(pool: asyncpg.Pool, service: str) -> int | None:
    hit = _service_cache.get(service)
    if hit is not None:
        return hit[0]
    project_id = await pool.fetchval("SELECT project_id FROM project_services WHERE service_name = $1", service)
    if project_id is not None:
        _service_cache.put(service, project_id)
    return project_id


async def claim_service(pool: asyncpg.Pool, service: str, project_id: int) -> int:
    """The service's project, assigning it to `project_id` if it has none."""
    current = await project_of(pool, service)
    if current is not None:
        return current
    owner = await pool.fetchval(
        """
        INSERT INTO project_services (service_name, project_id) VALUES ($1, $2)
        ON CONFLICT (service_name) DO UPDATE SET service_name = EXCLUDED.service_name
        RETURNING project_id
        """,
        service, project_id,
    )
    _service_cache.put(service, owner)
    return owner


async def move_service(pool: asyncpg.Pool, service: str, project_id: int) -> None:
    if await get_project(pool, project_id) is None:
        raise ProjectError("no such project")
    await pool.execute(
        """
        INSERT INTO project_services (service_name, project_id) VALUES ($1, $2)
        ON CONFLICT (service_name) DO UPDATE SET project_id = EXCLUDED.project_id
        """,
        service, project_id,
    )
    _service_cache.clear()


async def services_in(pool: asyncpg.Pool, project_ids: frozenset[int]) -> set[str]:
    rows = await pool.fetch(
        "SELECT service_name FROM project_services WHERE project_id = ANY($1::bigint[])", sorted(project_ids)
    )
    return {r["service_name"] for r in rows}


# --- API keys ----------------------------------------------------------------------

def _key_row(row) -> dict:
    return {
        "id": row["id"], "project_id": row["project_id"], "name": row["name"], "prefix": row["prefix"],
        "scopes": list(row["scopes"]), "created_at": row["created_at"], "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"],
    }


async def create_key(
    pool: asyncpg.Pool, project_id: int, name: str, scopes: list[str], created_by: int | None = None
) -> tuple[str, dict]:
    """(secret, key). The secret is returned once and never stored."""
    scopes = sorted(set(scopes or []))
    if not scopes or any(s not in SCOPES for s in scopes):
        raise ProjectError(f"scopes must be a non-empty subset of {', '.join(SCOPES)}")
    if await get_project(pool, project_id) is None:
        raise ProjectError("no such project")
    secret = KEY_PREFIX + secrets.token_urlsafe(32)
    row = await pool.fetchrow(
        """
        INSERT INTO api_keys (project_id, name, prefix, key_hash, scopes, created_by)
        VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
        """,
        project_id, (name or "key").strip()[:128], secret[:12], _hash(secret), scopes, created_by,
    )
    _key_cache.clear()  # misses are cached too
    return secret, _key_row(row)


async def list_keys(pool: asyncpg.Pool, project_id: int) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM api_keys WHERE project_id = $1 ORDER BY revoked_at IS NOT NULL, created_at DESC", project_id
    )
    return [_key_row(r) for r in rows]


async def revoke_key(pool: asyncpg.Pool, key_id: int, project_id: int | None = None) -> bool:
    revoked = await pool.fetchval(
        """
        UPDATE api_keys SET revoked_at = now()
        WHERE id = $1 AND revoked_at IS NULL AND ($2::bigint IS NULL OR project_id = $2)
        RETURNING id
        """,
        key_id, project_id,
    )
    _key_cache.clear()
    return revoked is not None


async def lookup_key(pool: asyncpg.Pool, key: str, now: datetime | None = None) -> ProjectKey | None:
    """The active project key matching `key`, or None."""
    if not key or not key.startswith(KEY_PREFIX):
        return None
    digest = _hash(key)
    hit = _key_cache.get(digest)
    if hit is not None:
        found = hit[0]
    else:
        row = await pool.fetchrow(
            "SELECT id, project_id, scopes FROM api_keys WHERE key_hash = $1 AND revoked_at IS NULL", digest
        )
        found = ProjectKey(row["id"], row["project_id"], frozenset(row["scopes"])) if row else None
        _key_cache.put(digest, found)
    if found is not None:
        now = now or datetime.now(timezone.utc)
        last = _last_used.get(found.id)
        if last is None or now - last >= LAST_USED_RESOLUTION:
            _last_used[found.id] = now
            await pool.execute("UPDATE api_keys SET last_used_at = $2 WHERE id = $1", found.id, now)
    return found


# --- members -------------------------------------------------------------------------

async def member_projects(pool: asyncpg.Pool, user_id: int) -> frozenset[int]:
    rows = await pool.fetch("SELECT project_id FROM project_members WHERE user_id = $1", user_id)
    return frozenset(r["project_id"] for r in rows)


async def add_member(pool: asyncpg.Pool, project_id: int, username: str) -> dict:
    user = await pool.fetchrow("SELECT id, username FROM users WHERE username = $1", (username or "").strip().lower())
    if user is None:
        raise ProjectError(f"no user {username!r}")
    if await get_project(pool, project_id) is None:
        raise ProjectError("no such project")
    await pool.execute(
        "INSERT INTO project_members (project_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        project_id, user["id"],
    )
    return {"project_id": project_id, "user_id": user["id"], "username": user["username"]}


async def remove_member(pool: asyncpg.Pool, project_id: int, user_id: int) -> bool:
    return (await pool.execute(
        "DELETE FROM project_members WHERE project_id = $1 AND user_id = $2", project_id, user_id
    )) == "DELETE 1"


async def list_members(pool: asyncpg.Pool, project_id: int) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT u.id AS user_id, u.username, u.is_admin, m.added_at
        FROM project_members m JOIN users u ON u.id = m.user_id
        WHERE m.project_id = $1 ORDER BY u.username
        """,
        project_id,
    )
    return [dict(r) for r in rows]
