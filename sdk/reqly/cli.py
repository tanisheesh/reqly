"""`reqly` command line: talk to a collector from CI or a shell.

    reqly deploy --service checkout-api                 # release from GITHUB_SHA, RENDER_GIT_COMMIT, ...
    reqly deploy --service checkout-api --release v42 --environment production
    reqly openapi push --service checkout-api openapi.yaml [--base-path /api]
    reqly ping                                          # is the collector up, and does the key work?

The collector and key come from --collector-url / --api-key or, like the
SDK, REQLY_COLLECTOR_URL / REQLY_API_KEY. Exits non-zero on any failure, so
a CI step fails visibly.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence
from urllib.parse import quote

import httpx

from .core.config import _detect_release

_TIMEOUT = httpx.Timeout(10.0)


def _client(args) -> httpx.Client:
    headers = {"X-Reqly-Key": args.api_key} if args.api_key else {}
    return httpx.Client(base_url=args.collector_url.rstrip("/"), headers=headers, timeout=_TIMEOUT)


def _fail(message: str) -> int:
    print(f"reqly: {message}", file=sys.stderr)
    return 1


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return f"{response.status_code} {detail or response.reason_phrase}"


def _deploy(args) -> int:
    release = args.release or _detect_release()
    if not release:
        return _fail("no release: pass --release, or set REQLY_RELEASE / a CI commit variable (GITHUB_SHA, ...)")
    body = {"service_name": args.service, "release": release}
    if args.environment:
        body["environment"] = args.environment
    if args.at:
        body["deployed_at"] = args.at
    with _client(args) as client:
        response = client.post("/v1/deployments", json=body)
    if response.status_code >= 400:
        return _fail(f"deploy marker refused: {_detail(response)}")
    print(f"reqly: marked {args.service} release {release}" + (f" in {args.environment}" if args.environment else ""))
    return 0


def _openapi_push(args) -> int:
    path = Path(args.spec)
    if not path.is_file():
        return _fail(f"no such file: {path}")
    content_type = "application/yaml" if path.suffix.lower() in (".yaml", ".yml") else "application/json"
    params = {"base_path": args.base_path} if args.base_path else None
    with _client(args) as client:
        response = client.put(
            f"/v1/services/{quote(args.service, safe='')}/openapi",
            content=path.read_bytes(), headers={"Content-Type": content_type}, params=params,
        )
    if response.status_code >= 400:
        return _fail(f"spec upload refused: {_detail(response)}")
    print(f"reqly: uploaded {path.name} for {args.service}")
    return 0


def _ping(args) -> int:
    with _client(args) as client:
        try:
            ready = client.get("/v1/ready")
        except httpx.HTTPError as exc:
            return _fail(f"collector unreachable at {args.collector_url}: {exc}")
        if ready.status_code != 200:
            return _fail(f"collector not ready: {_detail(ready)}")
        print(f"reqly: collector at {args.collector_url} is ready")
        if not args.api_key:
            print("reqly: no API key given (REQLY_API_KEY); not checked")
            return 0
        # A deploy marker for a service that can't exist checks the key
        # without writing anything: 422 means the key was accepted.
        probe = client.post("/v1/deployments", json={"service_name": "", "release": "x"})
    if probe.status_code in (401, 403):
        return _fail(f"the API key was refused: {_detail(probe)}")
    print("reqly: the API key is accepted for ingest")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reqly", description="Talk to a Reqly collector from CI or a shell.")
    parser.add_argument("--collector-url", default=os.environ.get("REQLY_COLLECTOR_URL") or "http://localhost:8000")
    parser.add_argument("--api-key", default=os.environ.get("REQLY_API_KEY") or None,
                        help="an ingest key or a project key with the ingest scope (default: REQLY_API_KEY)")
    commands = parser.add_subparsers(dest="command", required=True)

    deploy = commands.add_parser("deploy", help="record a deploy marker")
    deploy.add_argument("--service", default=os.environ.get("REQLY_SERVICE_NAME"), required="REQLY_SERVICE_NAME" not in os.environ)
    deploy.add_argument("--release", help="default: REQLY_RELEASE or the CI commit (GITHUB_SHA, RENDER_GIT_COMMIT, ...)")
    deploy.add_argument("--environment", default=os.environ.get("REQLY_ENVIRONMENT"))
    deploy.add_argument("--at", help="when it was deployed, ISO 8601 with a timezone (default: now)")
    deploy.set_defaults(run=_deploy)

    openapi = commands.add_parser("openapi", help="OpenAPI spec commands")
    openapi_commands = openapi.add_subparsers(dest="openapi_command", required=True)
    push = openapi_commands.add_parser("push", help="upload the service's OpenAPI spec (JSON or YAML)")
    push.add_argument("spec")
    push.add_argument("--service", default=os.environ.get("REQLY_SERVICE_NAME"), required="REQLY_SERVICE_NAME" not in os.environ)
    push.add_argument("--base-path", help="prefix the spec's paths, e.g. /api")
    push.set_defaults(run=_openapi_push)

    ping = commands.add_parser("ping", help="check the collector is up and the key is accepted")
    ping.set_defaults(run=_ping)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except httpx.HTTPError as exc:
        return _fail(f"request failed: {exc}")


if __name__ == "__main__":  # python -m reqly.cli
    sys.exit(main())
