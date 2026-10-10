"""Who may call what.

Every request resolves to a Principal: what it may do (scopes: ingest,
read, admin) and on which projects (None = all of them).

- Env ingest key (REQLY_INGEST_KEY): ingest + admin, every project.
- Env read key (REQLY_READ_KEY): read, every project -- only while
  PUBLIC_DASHBOARD is on. It ships in the dashboard bundle, so it is public.
- Project keys (rqk_...): their own scopes, their own project.
- Signed-in users (Authorization: Bearer <session>): admins read and
  change everything; other users read the projects they are members of.

Endpoints about one service (service_name in the path or query) check that
the principal's projects include that service's project, through the
service_name parameter of these dependencies. Sessions are bearer tokens
rather than cookies because the dashboard and the collector usually live on
different sites, where browsers block third-party cookies.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from .config import settings
from .db.pool import get_pool
from .projects import store as projects
from .users import accounts


@dataclass(frozen=True)
class Principal:
    kind: str  # "ingest-key", "read-key", "project-key", "user"
    scopes: frozenset[str]
    project_ids: frozenset[int] | None  # None: every project
    user: dict | None = None
    key_project_id: int | None = None  # the project a project key belongs to

    def can(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def all_projects(self) -> bool:
        return self.project_ids is None


def _key_matches(provided: str | None, expected: str) -> bool:
    # Constant-time comparison so the key can't be recovered byte-by-byte
    # from response timing.
    return bool(provided) and hmac.compare_digest(provided.encode(), expected.encode())


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def _user_principal(authorization: str | None) -> Principal | None:
    token = bearer_token(authorization)
    if token is None:
        return None
    pool = get_pool()
    user = await accounts.session_user(pool, token)
    if user is None:
        raise _unauthorized("session expired or signed out; sign in again")
    if user["is_admin"]:
        return Principal("user", frozenset({"read", "admin"}), None, user)
    return Principal("user", frozenset({"read"}), await projects.member_projects(pool, user["id"]), user)


async def _key_principal(key: str | None) -> Principal | None:
    if not key:
        return None
    if _key_matches(key, settings.ingest_key):
        return Principal("ingest-key", frozenset({"ingest", "admin"}), None)
    if _key_matches(key, settings.read_key):
        if not settings.public_dashboard:
            raise _unauthorized("this dashboard requires signing in")
        return Principal("read-key", frozenset({"read"}), None)
    found = await projects.lookup_key(get_pool(), key)
    if found is None:
        return None
    return Principal("project-key", found.scopes, frozenset({found.project_id}), key_project_id=found.project_id)


async def resolve(x_reqly_key: str | None, authorization: str | None) -> Principal:
    principal = await _user_principal(authorization) or await _key_principal(x_reqly_key)
    if principal is None:
        raise _unauthorized("invalid or missing X-Reqly-Key header")
    return principal


async def check_service(principal: Principal, service_name: str | None) -> None:
    """403 unless the principal's projects include the service's. A service
    with no project yet (no data) is only visible to all-project principals."""
    if not service_name or principal.all_projects:
        return
    project_id = await projects.project_of(get_pool(), service_name)
    if project_id is None or project_id not in principal.project_ids:
        raise _forbidden("this service belongs to a project you don't have access to")


async def allowed_services(principal: Principal) -> set[str] | None:
    """None: every service."""
    if principal.all_projects:
        return None
    return await projects.services_in(get_pool(), principal.project_ids)


async def writable_service(principal: Principal, service_name: str) -> bool:
    """Whether the principal may write events for the service. A service
    seen for the first time joins the writer's project (a project key's
    project; the default project for the env key)."""
    pool = get_pool()
    if principal.key_project_id is None:
        await projects.claim_service(pool, service_name, await projects.default_project_id(pool))
        return True
    owner = await projects.claim_service(pool, service_name, principal.key_project_id)
    return owner == principal.key_project_id


# --- dependencies ------------------------------------------------------------------

async def verify_ingest_key(x_REQLY_key: str | None = Header(default=None)) -> Principal:
    """Ingest: the env ingest key or a project key with the ingest scope.
    Which services a project key may write is checked per batch."""
    principal = await _key_principal(x_REQLY_key) if x_REQLY_key else None
    if principal is None or not principal.can("ingest"):
        raise _unauthorized("invalid or missing X-Reqly-Key header")
    return principal


async def verify_read_key(
    x_REQLY_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    service_name: str | None = None,
) -> Principal:
    """Read access, and to the service named in the path or query if any."""
    principal = await resolve(x_REQLY_key, authorization)
    if not principal.can("read"):
        raise _forbidden("this key can't read")
    await check_service(principal, service_name)
    return principal


async def verify_admin(
    x_REQLY_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    service_name: str | None = None,
) -> Principal:
    """Changing configuration (SLOs, specs, projects, keys)."""
    principal = await resolve(x_REQLY_key, authorization)
    if not principal.can("admin"):
        raise _forbidden("admins only")
    await check_service(principal, service_name)
    return principal


async def current_user(authorization: str | None = Header(default=None)) -> dict:
    principal = await _user_principal(authorization)
    if principal is None:
        raise _unauthorized("sign in first")
    return principal.user


# backwards-compat alias used by the ingest router
verify_api_key = verify_ingest_key
