"""All-in-one mode: the collector serving the built dashboard."""

import dataclasses

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import dashboard


@pytest.fixture
def site(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>shell</html>")
    (tmp_path / "assets" / "app-1a2b.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("nope")
    app = FastAPI()

    @app.get("/v1/services")
    async def services():
        return {"services": []}

    dashboard.mount_dashboard(app, str(tmp_path))
    return TestClient(app)


def test_files_the_app_shell_and_the_api(site):
    asset = site.get("/assets/app-1a2b.js")
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
    for path in ("/", "/services/shop", "/settings"):  # client-side routes
        page = site.get(path)
        assert page.text == "<html>shell</html>" and page.headers["cache-control"] == "no-cache"
    assert site.get("/v1/services").json() == {"services": []}  # the API wins
    assert site.get("/v1/nope").status_code == 404  # an unknown API path is not the dashboard
    assert site.get("/metrics-ish").status_code == 404


def test_no_way_out_of_the_directory(site):
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "nope" not in site.get(path).text


def test_config_points_at_this_origin_and_shares_the_read_key_only_when_public(monkeypatch):
    monkeypatch.setattr(dashboard, "settings", dataclasses.replace(
        dashboard.settings, public_dashboard=True, read_key="rk</script>"))
    js = dashboard._config_js()
    assert "collectorUrl: window.location.origin" in js and '"readKey": "rk\\u003c/script>"' in js
    monkeypatch.setattr(dashboard, "settings", dataclasses.replace(dashboard.settings, public_dashboard=False))
    assert "readKey" not in dashboard._config_js()


def test_a_missing_build_fails_loudly(tmp_path):
    with pytest.raises(RuntimeError, match="no index.html"):
        dashboard.mount_dashboard(FastAPI(), str(tmp_path))
