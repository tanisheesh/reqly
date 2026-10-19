"""Safe defaults outside demo mode, the demo defaults with REQLY_DEMO_MODE."""

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings

_VARS = ("REQLY_DEMO_MODE", "REQLY_INGEST_KEY", "REQLY_READ_KEY", "PUBLIC_DASHBOARD", "CORS_ORIGINS")


@pytest.fixture
def env(monkeypatch):
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_nothing_set_is_locked_down(env):
    s = Settings.from_env()
    assert (s.ingest_key, s.read_key, s.public_dashboard, s.cors_origins) == ("", "", False, [])
    assert s.missing_keys() == ["REQLY_INGEST_KEY", "REQLY_READ_KEY"]


def test_demo_mode_brings_back_the_demo_defaults(env):
    env.setenv("REQLY_DEMO_MODE", "true")
    s = Settings.from_env()
    assert (s.ingest_key, s.read_key, s.public_dashboard, s.cors_origins) == ("demo-key", "demo-read-key", True, ["*"])
    assert s.missing_keys() == []


def test_explicit_settings_win_either_way(env):
    env.setenv("REQLY_INGEST_KEY", "ik")
    env.setenv("REQLY_READ_KEY", "rk")
    env.setenv("PUBLIC_DASHBOARD", "true")
    env.setenv("CORS_ORIGINS", "https://dash.example")
    s = Settings.from_env()
    assert (s.ingest_key, s.read_key, s.public_dashboard, s.cors_origins) == ("ik", "rk", True, ["https://dash.example"])
    env.setenv("REQLY_DEMO_MODE", "true")
    env.setenv("PUBLIC_DASHBOARD", "false")
    assert Settings.from_env().public_dashboard is False


def test_the_collector_refuses_to_start_without_keys(monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, ingest_key="", read_key="rk"))
    with pytest.raises(RuntimeError, match="REQLY_INGEST_KEY must be set"):
        with TestClient(main.app):
            pass
