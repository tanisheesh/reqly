"""All-in-one mode: the collector serves the built dashboard (DASHBOARD_DIR)
on its own origin, so one container runs both and the browser needs no CORS
and no build-time collector URL."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from .config import settings

# Paths that belong to the API: an unknown one is a 404, not the dashboard.
_API_PREFIXES = ("v1/", "otlp/", "metrics", "docs", "redoc", "openapi.json")


def _config_js() -> str:
    """The dashboard's runtime config: this origin, and the read key only
    when the dashboard is public (otherwise viewers sign in)."""
    config = {"readKey": settings.read_key} if settings.public_dashboard else {}
    # "<" escaped so a value can't close a script tag.
    data = json.dumps(config).replace("<", "\\u003c")
    return f"window.__REQLY_CONFIG__ = Object.assign({{ collectorUrl: window.location.origin }}, {data});\n"


def mount_dashboard(app: FastAPI, directory: str) -> None:
    root = Path(directory).resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"DASHBOARD_DIR={directory} has no index.html (build the dashboard first)")

    @app.get("/config.js", include_in_schema=False)
    async def config_js() -> Response:
        return Response(_config_js(), media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard(path: str, request: Request) -> Response:
        if path.startswith(_API_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")
        target = (root / path).resolve()
        if path and target.is_file() and target.is_relative_to(root):
            # Vite fingerprints everything under assets/, so it never changes.
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(target, headers={"Cache-Control": cache})
        # Client-side routes (and the root) get the app shell.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
