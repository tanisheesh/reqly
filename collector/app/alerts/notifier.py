"""Alert messages and delivery: Slack, Discord, Microsoft Teams, PagerDuty,
Opsgenie, a generic webhook and email.

Messages are built from the deterministic statistics only (no LLM in the
alert path), so they're instant, free and never invented.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, NamedTuple

import httpx

logger = logging.getLogger("reqly.collector")

OPENED = "opened"
STILL_FIRING = "still_firing"
RESOLVED = "resolved"


@dataclass(frozen=True)
class Email:
    """SMTP, or Resend's API when resend_api_key is set."""

    to: tuple[str, ...]
    sender: str
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = field(default=None, repr=False)
    smtp_starttls: bool = True
    resend_api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Channels:
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    webhook_url: str | None = None
    dashboard_url: str | None = None
    teams_webhook_url: str | None = None
    pagerduty_routing_key: str | None = field(default=None, repr=False)
    opsgenie_api_key: str | None = field(default=None, repr=False)
    opsgenie_api_url: str = "https://api.opsgenie.com"
    email: Email | None = None

    @property
    def any(self) -> bool:
        return bool(
            self.slack_webhook_url or self.discord_webhook_url or self.webhook_url or self.teams_webhook_url
            or self.pagerduty_routing_key or self.opsgenie_api_key or self.email
        )


def channels_from_settings(settings) -> Channels:
    email = None
    if settings.alert_email_to and settings.alert_email_from and (settings.smtp_host or settings.resend_api_key):
        email = Email(
            to=tuple(settings.alert_email_to), sender=settings.alert_email_from,
            smtp_host=settings.smtp_host, smtp_port=settings.smtp_port, smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password, smtp_starttls=settings.smtp_starttls,
            resend_api_key=settings.resend_api_key,
        )
    return Channels(
        slack_webhook_url=settings.alert_slack_webhook_url,
        discord_webhook_url=settings.alert_discord_webhook_url,
        webhook_url=settings.alert_webhook_url,
        dashboard_url=settings.dashboard_url,
        teams_webhook_url=settings.alert_teams_webhook_url,
        pagerduty_routing_key=settings.alert_pagerduty_routing_key,
        opsgenie_api_key=settings.alert_opsgenie_api_key,
        opsgenie_api_url=settings.alert_opsgenie_api_url,
        email=email,
    )


class Delivery(NamedTuple):
    url: str
    body: dict
    headers: dict | None = None


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


def _plain(text: str) -> str:
    """The message without chat markup, for email and incident titles."""
    return text.replace("*", "").replace("`", "")


def _body_lines(text: str) -> tuple[str, list[str]]:
    """(title, detail lines) of a message, without the dashboard link line."""
    lines = [line for line in text.split("\n") if not line.startswith("<")]
    return lines[0], lines[1:]


def _teams_card(text: str, dashboard_url: str | None, event: str) -> dict:
    """A Teams Workflows ("When a Teams webhook request is received") message
    with an Adaptive Card. Cards use **bold**, so *bold* is doubled."""
    title, details = _body_lines(text)
    body: list[dict[str, Any]] = [{"type": "TextBlock", "text": title.replace("*", "**"), "wrap": True, "weight": "Bolder"}]
    body += [{"type": "TextBlock", "text": line.replace("*", "**"), "wrap": True, "spacing": "Small"} for line in details]
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.4", "body": body,
    }
    if dashboard_url and event != RESOLVED:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open dashboard", "url": dashboard_url}]
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def _incident_key(alert: dict) -> str:
    """One PagerDuty incident / Opsgenie alert per Reqly alert: re-sends
    update it and the resolve closes it."""
    return f"reqly-alert-{alert['id']}"


def build_payloads(channels: Channels, event: str, alert: dict) -> list[Delivery]:
    """The HTTP deliveries for the configured channels (email is sent
    separately). `alert` has service_name, route, details and
    id/opened_at/resolved_at."""
    text = format_text(event, alert["service_name"], alert["route"], alert["details"], channels.dashboard_url)
    title = _plain(_body_lines(text)[0])
    payloads: list[Delivery] = []
    if channels.slack_webhook_url:
        payloads.append(Delivery(channels.slack_webhook_url, {"text": text}))
    if channels.discord_webhook_url:
        # Discord has no <url|label> links; drop that line.
        payloads.append(Delivery(channels.discord_webhook_url, {
            "content": text.split("\n<")[0],
            "allowed_mentions": {"parse": []},  # no @everyone / role pings from data
        }))
    if channels.teams_webhook_url:
        payloads.append(Delivery(channels.teams_webhook_url, _teams_card(text, channels.dashboard_url, event)))
    if channels.pagerduty_routing_key:
        body: dict[str, Any] = {
            "routing_key": channels.pagerduty_routing_key,
            "event_action": "resolve" if event == RESOLVED else "trigger",
            "dedup_key": _incident_key(alert),
        }
        if event != RESOLVED:
            body["payload"] = {
                "summary": title[:1024], "source": _clean(alert["service_name"]), "severity": "error",
                "component": _clean(alert["route"]), "group": alert.get("kind") or "anomaly",
                "custom_details": {"message": _plain(text.split("\n<")[0]), "details": _clean(alert["details"])},
            }
            if channels.dashboard_url:
                body["links"] = [{"href": channels.dashboard_url, "text": "Open dashboard"}]
        payloads.append(Delivery("https://events.pagerduty.com/v2/enqueue", body))
    if channels.opsgenie_api_key:
        api = channels.opsgenie_api_url.rstrip("/")
        headers = {"Authorization": f"GenieKey {channels.opsgenie_api_key}"}
        if event == RESOLVED:
            payloads.append(Delivery(
                f"{api}/v2/alerts/{_incident_key(alert)}/close?identifierType=alias", {"source": "Reqly"}, headers,
            ))
        else:  # the same alias again just bumps the existing alert's count
            payloads.append(Delivery(f"{api}/v2/alerts", {
                "message": title[:130], "alias": _incident_key(alert), "source": "Reqly", "priority": "P2",
                "description": _plain(text)[:15000], "entity": _clean(alert["service_name"]),
                "details": {"service": _clean(alert["service_name"]), "route": _clean(alert["route"]),
                            "kind": alert.get("kind") or "anomaly"},
            }, headers))
    if channels.webhook_url:
        payloads.append(Delivery(channels.webhook_url, {"event": f"alert.{event}", "text": text, "alert": alert}))
    return payloads


def build_email(email: Email, event: str, alert: dict, dashboard_url: str | None) -> EmailMessage:
    text = format_text(event, alert["service_name"], alert["route"], alert["details"], None)
    message = EmailMessage()
    message["Subject"] = f"[Reqly] {_plain(_body_lines(text)[0])}"[:200]
    message["From"] = email.sender
    message["To"] = ", ".join(email.to)
    body = _plain(text)
    if dashboard_url and event != RESOLVED:
        body += f"\n\nOpen dashboard: {dashboard_url}"
    message.set_content(body)
    return message


def _send_smtp(email: Email, message: EmailMessage) -> None:
    with smtplib.SMTP(email.smtp_host or "localhost", email.smtp_port, timeout=10) as smtp:
        if email.smtp_starttls:
            smtp.starttls()
        if email.smtp_username:
            smtp.login(email.smtp_username, email.smtp_password or "")
        smtp.send_message(message)


async def _send_email(email: Email, message: EmailMessage, client: httpx.AsyncClient) -> None:
    if email.resend_api_key:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {email.resend_api_key}"},
            json={"from": email.sender, "to": list(email.to), "subject": message["Subject"],
                  "text": message.get_content()},
        )
        response.raise_for_status()
    else:
        await asyncio.to_thread(_send_smtp, email, message)


async def send(channels: Channels, event: str, alert: dict, client: httpx.AsyncClient | None = None) -> int:
    """Delivers to every configured channel; returns how many succeeded. A
    failing channel is logged and doesn't stop the others."""
    payloads = build_payloads(channels, event, alert)
    if not payloads and channels.email is None:
        return 0
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    delivered = 0
    try:
        for url, body, headers in payloads:
            try:
                response = await client.post(url, json=body, headers=headers)
                response.raise_for_status()
                delivered += 1
            except httpx.HTTPError as exc:
                logger.warning("alert delivery failed (%s): %s", url.split("/")[2], exc)
        if channels.email is not None:
            try:
                await _send_email(channels.email, build_email(channels.email, event, alert, channels.dashboard_url), client)
                delivered += 1
            except (httpx.HTTPError, OSError, smtplib.SMTPException) as exc:
                logger.warning("alert email failed: %s", exc)
    finally:
        if own_client:
            await client.aclose()
    return delivered
