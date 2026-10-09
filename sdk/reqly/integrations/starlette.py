from __future__ import annotations

from ..core.client import ReqlyClient
from .fastapi import ReqlyASGIMiddleware


def _match_template(routes, scope: dict, prefix: str = "") -> str | None:
    """Route template for the request, by re-running Starlette's own route
    matching (plain Starlette, unlike FastAPI, doesn't record the matched
    route in the scope). Mounted sub-routers are followed and their prefix
    prepended, e.g. Mount("/api", routes=[Route("/users/{id}")]) gives
    "/api/users/{id}"."""
    from starlette.routing import Match

    for route in routes:
        try:
            match, child_scope = route.matches(scope)
        except Exception:
            continue
        if match != Match.FULL:
            continue
        path = getattr(route, "path", "") or ""
        sub_routes = getattr(route, "routes", None)
        if sub_routes:  # Mount / Host with its own router
            # Newer Starlette versions don't expose the mount prefix as
            # .path; fall back to the part of the path the mount consumed.
            mount_prefix = path or child_scope.get("root_path", "")[len(scope.get("root_path", "")):]
            return _match_template(sub_routes, {**scope, **child_scope}, prefix + mount_prefix)
        return prefix + path
    return None


def instrument_starlette(app, client: ReqlyClient) -> None:
    def resolve(scope: dict) -> str | None:
        return _match_template(app.router.routes, scope)

    app.add_middleware(ReqlyASGIMiddleware, client=client, route_resolver=resolve)
