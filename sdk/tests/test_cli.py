"""The `reqly` command line, against a stand-in collector."""

import json

import httpx
import pytest

from reqly import cli


@pytest.fixture
def collector(monkeypatch):
    seen = []
    replies = {}

    def handler(request):
        seen.append(request)
        status, body = replies.get((request.method, request.url.path), (200, {}))
        return httpx.Response(status, json=body)

    def client(args):
        headers = {"X-Reqly-Key": args.api_key} if args.api_key else {}
        return httpx.Client(base_url=args.collector_url, headers=headers, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cli, "_client", client)
    for name in ("REQLY_RELEASE", "GITHUB_SHA", "RENDER_GIT_COMMIT", "REQLY_SERVICE_NAME", "REQLY_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    return seen, replies


def test_deploy_sends_a_marker_with_the_ci_release(collector, monkeypatch, capsys):
    seen, _ = collector
    monkeypatch.setenv("GITHUB_SHA", "a1b2c3d")
    assert cli.main(["--api-key", "k", "deploy", "--service", "shop", "--environment", "production"]) == 0
    [request] = seen
    assert (request.method, request.url.path, request.headers["x-reqly-key"]) == ("POST", "/v1/deployments", "k")
    assert json.loads(request.content) == {"service_name": "shop", "release": "a1b2c3d", "environment": "production"}
    assert "marked shop release a1b2c3d in production" in capsys.readouterr().out


def test_deploy_without_a_release_or_with_a_refusal_fails(collector, capsys):
    _, replies = collector
    assert cli.main(["deploy", "--service", "shop"]) == 1
    assert "no release" in capsys.readouterr().err
    replies[("POST", "/v1/deployments")] = (403, {"detail": "this service belongs to another project"})
    assert cli.main(["deploy", "--service", "shop", "--release", "v1"]) == 1
    assert "403 this service belongs to another project" in capsys.readouterr().err


def test_openapi_push_sends_yaml_with_a_base_path(collector, tmp_path):
    seen, _ = collector
    spec = tmp_path / "openapi.yaml"
    spec.write_text("openapi: 3.0.0\npaths: {}\n")
    assert cli.main(["--api-key", "k", "openapi", "push", "--service", "my api", str(spec), "--base-path", "/api"]) == 0
    [request] = seen
    assert request.method == "PUT" and request.url.raw_path == b"/v1/services/my%20api/openapi?base_path=%2Fapi"
    assert request.headers["content-type"] == "application/yaml" and request.content.startswith(b"openapi: 3.0.0")
    assert cli.main(["openapi", "push", "--service", "x", str(tmp_path / "missing.json")]) == 1


def test_ping_checks_readiness_and_the_key(collector, capsys):
    _, replies = collector
    replies[("POST", "/v1/deployments")] = (422, {"detail": []})  # accepted key, invalid probe body
    assert cli.main(["--api-key", "k", "ping"]) == 0
    assert "the API key is accepted" in capsys.readouterr().out
    replies[("POST", "/v1/deployments")] = (401, {"detail": "invalid or missing X-Reqly-Key header"})
    assert cli.main(["--api-key", "bad", "ping"]) == 1
    replies[("GET", "/v1/ready")] = (503, {"detail": "database unreachable"})
    assert cli.main(["ping"]) == 1
