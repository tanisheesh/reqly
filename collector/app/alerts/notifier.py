"""Alert messages and delivery to Slack, Discord and a generic webhook.

Messages are built from the deterministic statistics only (no LLM in the
alert path), so they're instant, free and never invented.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger("reqly.collector")

OPENED = "opened"
STILL_FIRING = "still_firing"
RESOLVED = "resolved"


@dataclass(frozen=True)
class Channels:
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    webhook_url: str | None = None
    dashboard_url: str | None = None

    @property
    def any(self) -> bool:
        return bool(self.slack_webhook_url or self.discord_webhook_url or self.webhook_url)


# Characters a sender could use to break out of `code` spans or to write
# Slack control sequences (<!channel>, <https://...|label>) into a message.
_UNSAFE = str.maketrans({"`": "'", "<": "‹", ">": "›"})


def _clean(value):
    """Service names, routes, releases, hosts, consumer ids and models come
    from whoever holds an ingest key: neutralized before they reach chat."""
    if isinstance(value, str):
        return value.translate(_UNSAFE)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _ms(value) -> str:
    return "—" if value is None else f"{value:.0f}ms"


def _pct(value) -> str:
    return "—" if value is None else f"{value:.1%}"


def _format_slo(event: str, service_name: str, details: dict) -> str:
    slo, status = details["slo"], details["status"]
    scope = f"`{slo['route']}`" if slo.get("route") else "all routes"
    if slo["objective"] == "availability":
        goal = f"{slo['target']:.2%} of requests without errors"
    else:
        goal = f"{slo['target']:.2%} of requests under {slo['latency_threshold_ms']:.0f}ms"
    if event == RESOLVED:
        return f"✅ *Resolved* — SLO `{slo['name']}` (`{service_name}`) is no longer burning its error budget."
    speed = "Fast" if status["state"] == "fast_burn" else "Slow"
    icon = "🔥" if status["state"] == "fast_burn" else "🟠"
    burns = status["burn_rates"]
    budget = status["budget_remaining"]
    sli = status["sli"]
    return "\n".join([
        f"{icon} *{speed} burn* — SLO `{slo['name']}` on `{service_name}` {scope} ({goal}, {slo['window_days']}d)",
        f"• burning error budget {burns['1h']:g}× (1h) / {burns['5m']:g}× (5m) / {burns['6h']:g}× (6h) the sustainable rate",
        f"• SLI {'—' if sli is None else f'{sli:.3%}'} · "
        f"{'—' if budget is None else f'{max(budget, 0):.0%}'} of the error budget left",
    ])


def _usd(value) -> str:
    if value is None:
        return "—"
    if value >= 1:
        return f"${value:,.2f}"
    return "$" + f"{value:.4f}".rstrip("0").rstrip(".")


def _format_llm_cost(event: str, service_name: str, route: str, details: dict) -> str:
    if event == RESOLVED:
        return f"✅ *Resolved* — LLM cost on `{service_name}` `{route}` is back within its normal range."
    icon = "💸" if event == OPENED else "🟠"
    title = "LLM cost spike" if event == OPENED else "LLM cost still high"
    why = "cost per request" if details.get("cause") == "unit_cost" else "request volume"
    lines = [
        f"{icon} *{title}* — `{service_name}` `{route}` ({details['day_of_week']} {details['hour_range']} UTC)",
        f"• {_usd(details['observed_cost_usd'])} this hour vs {_usd(details['baseline_cost_usd'])} usual "
        f"(+{_usd(details['extra_cost_usd'])}) · {_usd(details['observed_cost_per_1k_requests'])} vs "
        f"{_usd(details['baseline_cost_per_1k_requests'])} per 1k requests",
        f"• driven by {why}",
    ]
    for driver in (details.get("drivers") or [])[:3]:
        lines.append(f"• {driver['text']}")
    mix = details.get("model_mix")
    if mix:
        lines.append(f"• {mix['text']}")
    return "\n".join(lines)


def format_text(event: str, service_name: str, route: str, details: dict, dashboard_url: str | None = None) -> str:
    """One plain-text message used for Slack and Discord (both render the
    *bold* / `code` subset the same way closely enough)."""
    service_name, route, details = _clean(service_name), _clean(route), _clean(details)
    if details.get("kind") in ("slo", "llm_cost"):
        if details["kind"] == "slo":
            text = _format_slo(event, service_name, details)
        else:
            text = _format_llm_cost(event, service_name, route, details)
        return f"{text}\n<{dashboard_url}|Open dashboard>" if dashboard_url and event != RESOLVED else text
    if event == RESOLVED:
        return f"✅ *Resolved* — `{service_name}` `{route}` is back within its normal range."

    icon = "🔴" if event == OPENED else "🟠"
    title = "Anomaly" if event == OPENED else "Still anomalous"
    lines = [
        f"{icon} *{title}* — `{service_name}` `{route}` "
        f"({details['day_of_week']} {details['hour_range']} UTC, z={details['z_score']})",
        f"• error rate {_pct(details['observed_error_rate'])} vs {_pct(details['baseline_error_rate'])} usual · "
        f"p95 {_ms(details['observed_p95_ms'])} vs {_ms(details['baseline_p95_ms'])} usual",
    ]
    context = details.get("release_context") or {}
    if context.get("is_new_release"):
        line = f"• running release `{context['release']}`, first seen {context['release_first_seen_at']}"
        before, after = context.get("before"), context.get("after")
        if context.get("previous_release") and before and after:
            line += (
                f" — vs `{context['previous_release']}`: errors {_pct(before['error_rate'])} → "
                f"{_pct(after['error_rate'])}, p95 {_ms(before['p95_ms'])} → {_ms(after['p95_ms'])}"
            )
        lines.append(line)
    for hint in details.get("hints") or []:
        lines.append(f"• {hint['text']}")
    affected = details.get("affected_consumers")
    if affected and affected.get("affected"):
        line = f"• {affected['affected']} of {affected['active']} consumers got errors"
        top = affected.get("top") or []
        if top:
            line += " — most: " + ", ".join(f"`{c['consumer_id']}` ({c['errors']})" for c in top[:3])
        lines.append(line)
    if dashboard_url:
        lines.append(f"<{dashboard_url}|Open dashboard>")
    return "\n".join(lines)


def build_payloads(channels: Channels, event: str, alert: dict) -> list[tuple[str, dict]]:
    """(url, json body) per configured channel. `alert` has service_name,
    route, details and id/opened_at/resolved_at."""
    text = format_text(event, alert["service_name"], alert["route"], alert["details"], channels.dashboard_url)
    payloads = []
    if channels.slack_webhook_url:
        payloads.append((channels.slack_webhook_url, {"text": text}))
    if channels.discord_webhook_url:
        # Discord has no <url|label> links; drop that line.
        payloads.append((channels.discord_webhook_url, {
            "content": text.split("\n<")[0],
            "allowed_mentions": {"parse": []},  # no @everyone / role pings from data
        }))
    if channels.webhook_url:
        payloads.append((channels.webhook_url, {"event": f"alert.{event}", "text": text, "alert": alert}))
    return payloads


async def send(channels: Channels, event: str, alert: dict, client: httpx.AsyncClient | None = None) -> int:
    """Delivers to every configured channel; returns how many succeeded. A
    failing channel is logged and doesn't stop the others."""
    payloads = build_payloads(channels, event, alert)
    if not payloads:
        return 0
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    delivered = 0
    try:
        for url, body in payloads:
            try:
                response = await client.post(url, json=body)
                response.raise_for_status()
                delivered += 1
            except httpx.HTTPError as exc:
                logger.warning("alert delivery failed (%s): %s", url.split("/")[2], exc)
    finally:
        if own_client:
            await client.aclose()
    return delivered
