from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import verify_admin, verify_read_key
from ..db.pool import get_pool
from ..openapi import store
from ..openapi.drift import SpecError, spec_base_path, spec_operations, validate_spec

router = APIRouter()

MAX_SPEC_BYTES = 5 * 1024 * 1024


def parse_spec(body: bytes, content_type: str) -> dict:
    if len(body) > MAX_SPEC_BYTES:
        raise SpecError(f"spec larger than {MAX_SPEC_BYTES // (1024 * 1024)} MB")
    text = body.decode("utf-8", errors="replace")
    if "yaml" in content_type or not text.lstrip().startswith(("{", "[")):
        import yaml

        try:
            return validate_spec(yaml.safe_load(text))
        except yaml.YAMLError as exc:
            raise SpecError(f"invalid YAML: {exc}") from exc
    try:
        return validate_spec(json.loads(text))
    except json.JSONDecodeError as exc:
        raise SpecError(f"invalid JSON: {exc}") from exc


# Uploading needs the ingest key (the SDK and CI hold it); the read key ships
# in the dashboard bundle.
@router.put("/v1/services/{service_name}/openapi", dependencies=[Depends(verify_admin)])
async def put_spec(
    service_name: str,
    request: Request,
    base_path: str | None = Query(default=None, max_length=256),
):
    """Stores the service's OpenAPI (3.x) or Swagger (2.0) spec, JSON or YAML.
    base_path: prefix the app serves the spec's paths under (default: the
    spec's Swagger basePath, else none)."""
    try:
        spec = parse_spec(await request.body(), request.headers.get("content-type", ""))
    except SpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if base_path is None:
        base_path = spec_base_path(spec)
    saved = await store.save_spec(get_pool(), service_name, spec, base_path)
    return {
        "service_name": service_name,
        "operations": len(spec_operations(spec, base_path)),
        "base_path": base_path,
        **saved,
    }


@router.delete("/v1/services/{service_name}/openapi", dependencies=[Depends(verify_admin)])
async def delete_spec(service_name: str):
    if not await store.delete_spec(get_pool(), service_name):
        raise HTTPException(status_code=404, detail="no spec for this service")
    return {"deleted": service_name}


@router.get("/v1/services/{service_name}/openapi/drift", dependencies=[Depends(verify_read_key)])
async def drift(service_name: str):
    """Undocumented, dead and deprecated-but-used operations over the last 30 days."""
    report = await store.drift_report(get_pool(), service_name)
    if report is None:
        raise HTTPException(status_code=404, detail="no OpenAPI spec uploaded for this service")
    return report
