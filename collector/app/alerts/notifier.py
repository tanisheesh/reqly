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


def _ms(value) -> str:
    return "—" if value is None else f"{value:.0f}ms"


def _pct(value) -> str:
    return "—" if value is None else f"{value:.1%}"


def format_text(event: str, service_name: str, route: str, details: dict, dashboard_url: str | None = None) -> str:
    """One plain-text message used for Slack and Discord (both render the
    *bold* / `code` subset the same way closely enough)."""
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
        payloads.append((channels.discord_webhook_url, {"content": text.split("\n<")[0]}))
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
