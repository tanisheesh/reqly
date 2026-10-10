from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import Principal, verify_admin, verify_read_key
from ..db.pool import get_pool
from ..projects import store

router = APIRouter()


class ProjectIn(BaseModel):
    slug: str = Field(min_length=1, max_length=63)
    name: str | None = Field(default=None, max_length=128)


class ServiceIn(BaseModel):
    service_name: str = Field(min_length=1, max_length=128)


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[str] = Field(min_length=1, max_length=3)


class MemberIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


def _require_global_admin(principal: Principal) -> None:
    """Projects, service ownership and membership span projects: only an
    admin user or the env ingest key."""
    if not (principal.can("admin") and principal.all_projects):
        raise HTTPException(status_code=403, detail="only an admin of every project can do this")


def _require_project_admin(principal: Principal, project_id: int) -> None:
    if not principal.can("admin") or not (principal.all_projects or project_id in principal.project_ids):
        raise HTTPException(status_code=403, detail="you can't manage this project")


async def _project_or_404(project_id: int) -> dict:
    project = await store.get_project(get_pool(), project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="no such project")
    return project


def _bad_request(exc: store.ProjectError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/v1/projects")
async def list_projects(principal: Principal = Depends(verify_read_key)):
    """The projects the caller can see, with their services."""
    return {"projects": await store.list_projects(get_pool(), principal.project_ids)}


@router.post("/v1/projects")
async def create_project(body: ProjectIn, principal: Principal = Depends(verify_admin)):
    _require_global_admin(principal)
    try:
        return await store.create_project(get_pool(), body.slug, body.name or body.slug)
    except store.ProjectError as exc:
        raise _bad_request(exc)


@router.put("/v1/projects/{project_id}/services")
async def move_service(project_id: int, body: ServiceIn, principal: Principal = Depends(verify_admin)):
    """Assigns a service (and its existing data) to this project."""
    _require_global_admin(principal)
    try:
        await store.move_service(get_pool(), body.service_name, project_id)
    except store.ProjectError as exc:
        raise _bad_request(exc)
    return {"service_name": body.service_name, "project_id": project_id}


@router.get("/v1/projects/{project_id}/keys")
async def list_keys(project_id: int, principal: Principal = Depends(verify_admin)):
    _require_project_admin(principal, project_id)
    await _project_or_404(project_id)
    return {"keys": await store.list_keys(get_pool(), project_id)}


@router.post("/v1/projects/{project_id}/keys")
async def create_key(project_id: int, body: KeyIn, principal: Principal = Depends(verify_admin)):
    """Returns the key once; only its hash is stored."""
    _require_project_admin(principal, project_id)
    await _project_or_404(project_id)
    try:
        secret, key = await store.create_key(
            get_pool(), project_id, body.name, body.scopes,
            created_by=principal.user["id"] if principal.user else None,
        )
    except store.ProjectError as exc:
        raise _bad_request(exc)
    return {"key": secret, **key}


@router.post("/v1/keys/{key_id}/revoke")
async def revoke_key(key_id: int, principal: Principal = Depends(verify_admin)):
    project_id = await get_pool().fetchval("SELECT project_id FROM api_keys WHERE id = $1", key_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="no such key")
    _require_project_admin(principal, project_id)
    if not await store.revoke_key(get_pool(), key_id, project_id):
        raise HTTPException(status_code=409, detail="key is already revoked")
    return {"revoked": key_id}


@router.get("/v1/projects/{project_id}/members")
async def list_members(project_id: int, principal: Principal = Depends(verify_admin)):
    _require_global_admin(principal)
    await _project_or_404(project_id)
    return {"members": await store.list_members(get_pool(), project_id)}


@router.post("/v1/projects/{project_id}/members")
async def add_member(project_id: int, body: MemberIn, principal: Principal = Depends(verify_admin)):
    _require_global_admin(principal)
    try:
        return await store.add_member(get_pool(), project_id, body.username)
    except store.ProjectError as exc:
        raise _bad_request(exc)


@router.delete("/v1/projects/{project_id}/members/{user_id}")
async def remove_member(project_id: int, user_id: int, principal: Principal = Depends(verify_admin)):
    _require_global_admin(principal)
    if not await store.remove_member(get_pool(), project_id, user_id):
        raise HTTPException(status_code=404, detail="not a member")
    return {"removed": user_id}
