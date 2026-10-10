"""Ask Reqly tools against TimescaleDB (skipped when unreachable)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.ask import tools
from app.db import pool as pool_module
from app.db import queries
from tests.test_queries_db import _db_reachable, _refresh_all, aggregate_mode  # noqa: F401

pytestmark = pytest.mark.skipif(not _db_reachable(), reason="TimescaleDB not reachable")


def _rows(service, now):
    """Three days of /orders (and some /health) on two hosts at 1% errors
    and 100ms. Six hours ago release v2 rolled out on host h2: from then on
    half of h2's /orders requests fail with a 500 after 400ms."""
    rows = []
    t = now - timedelta(days=3)
    i = 0
    while t < now - timedelta(minutes=5):
        host = "h1" if i % 2 else "h2"
        broken = t >= now - timedelta(hours=6) and host == "h2"
        route = "/health" if i % 5 == 0 else "/orders"
        bad = route == "/orders" and ((broken and i % 4 == 0) or i % 100 == 1)
        rows.append(queries.event_row(
            event_id=str(uuid.uuid4()), time=t, service_name=service, method="POST",
            route=route, status_code=500 if bad else 200,
            duration_ms=400.0 if broken and route == "/orders" else 100.0,
            is_error=bad, error_type="DBTimeout" if bad else None, host=host,
            release="v2" if broken else "v1", environment="production",
        ))
        t += timedelta(minutes=1)
        i += 1
    return rows


def _run(service, now, calls):
    async def run():
        pool = await pool_module.create_pool()
        try:
            rows = _rows(service, now)
            await queries.insert_events(pool, rows)
            await queries.record_deployments(pool, rows)
            await _refresh_all(pool)
            return [await tools.run_tool(pool, service, name, args, now) for name, args in calls]
        finally:
            await pool.execute("DELETE FROM request_events WHERE service_name = $1", service)
            await pool.execute("DELETE FROM deployments WHERE service_name = $1", service)
            await pool_module.close_pool()

    return asyncio.run(run())


def _iso(t):
    return t.isoformat()


def test_stats_and_compare(aggregate_mode):  # noqa: F811
    service = f"test-ask-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    six_h = now - timedelta(hours=6)
    total, by_route, hourly, too_long, compare, compare_routes = _run(service, now, [
        ("get_stats", {"start": _iso(now - timedelta(days=3, hours=1))}),
        ("get_stats", {"group_by": "route"}),
        ("get_stats", {"route": "/orders", "group_by": "hour", "start": _iso(now - timedelta(hours=12))}),
        ("get_stats", {"group_by": "hour", "start": _iso(now - timedelta(days=5))}),
        ("compare_periods", {
            "route": "/orders",
            "before_start": _iso(six_h - timedelta(days=1)), "before_end": _iso(now - timedelta(days=1)),
            "after_start": _iso(six_h), "after_end": _iso(now),
        }),
        ("compare_periods", {
            "by_route": True,
            "before_start": _iso(six_h - timedelta(days=1)), "before_end": _iso(now - timedelta(days=1)),
            "after_start": _iso(six_h), "after_end": _iso(now),
        }),
    ])

    assert total["stats"]["requests"] == pytest.approx(3 * 24 * 60 - 5, abs=2)
    assert [r["route"] for r in by_route["rows"]] == ["/orders", "/health"]
    assert by_route["rows"][1]["errors"] == 0

    assert 12 <= len(hourly["rows"]) <= 13
    # the hours before the rollout are healthy, the ones after are not
    assert hourly["rows"][1]["error_rate"] < 0.05
    assert hourly["rows"][-2]["error_rate"] > 0.08
    assert "at most 72h" in too_long["error"]

    assert compare["before"]["error_rate"] < 0.03
    assert compare["after"]["error_rate"] > 0.08
    assert compare["after"]["p95_ms"] == pytest.approx(400, rel=0.02)
    assert compare["change"]["p95_ms"]["ratio"] == pytest.approx(4, rel=0.05)

    # route by route: /orders broke, /health didn't
    changed = compare_routes["routes_most_changed"]
    assert [r["route"] for r in changed] == ["/orders", "/health"]
    assert changed[0]["change"]["error_rate"]["delta"] > 0.05
    assert changed[1]["change"]["error_rate"]["delta"] == 0


def test_breakdown_releases_and_retention():
    service = f"test-ask-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    last_6h = {"start": _iso(now - timedelta(hours=6)), "route": "/orders"}
    by_host, by_release, by_status, releases, too_old = _run(service, now, [
        ("get_breakdown", {**last_6h, "dimension": "host"}),
        ("get_breakdown", {**last_6h, "dimension": "release"}),
        ("get_breakdown", {**last_6h, "dimension": "status_code"}),
        ("list_releases", {}),
        ("get_breakdown", {"dimension": "host", "start": _iso(now - timedelta(days=40)),
                           "end": _iso(now - timedelta(days=30))}),
    ])

    hosts = {v["value"]: v for v in by_host["values"]}
    assert hosts["h2"]["share_of_errors"] > 0.85
    assert hosts["h2"]["share_of_requests"] == pytest.approx(0.5, abs=0.05)
    assert hosts["h2"]["p95_ms"] == 400
    assert {v["value"] for v in by_release["values"]} == {"v1", "v2"}
    assert {v["value"] for v in by_status["values"]} == {"200", "500"}

    assert [r["release"] for r in releases["releases"]] == ["v2", "v1"]
    assert releases["releases"][0]["first_seen"] == (now - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%MZ")

    assert "empty time range" in too_old["error"]
