"""Ask Reqly: answers a natural-language question about one service by
letting the model call the read-only tools in tools.py, then write an
answer from their results."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from ..config import settings
from ..db import queries
from .tools import TOOL_SCHEMAS, run_tool
from .verify import unverified_numbers

logger = logging.getLogger("reqly.collector")

MAX_TOOL_CALLS = 6
MAX_ROUNDS = 5
MAX_ROUTES_IN_PROMPT = 40
MAX_RELEASES_IN_PROMPT = 5

SYSTEM_PROMPT = """You are Reqly's on-call analyst. You answer questions about the HTTP API \
service "{service}" using only the numbers returned by your tools.

Now: {now} UTC ({weekday}). All timestamps are UTC.
Dates of the last 8 days -- use these, don't compute weekdays yourself ("last Monday" \
means the most recent Monday in this list):
{calendar}
Data: latency/error aggregates go back {aggregate_days} days; per-host/release/status \
breakdowns go back 14 days.
Known routes: {routes}
Recent releases (first seen): {releases}

Rules:
- Call tools to get the data; never guess or invent numbers. Prefer one well-chosen call \
over many; you have at most {max_calls} tool calls.
- Resolve relative times ("yesterday", "last night", "since the deploy") into explicit \
ISO 8601 ranges yourself. "The last N hours/days" is a rolling window ending now, not \
calendar days.
- For questions about a time of day ("morning", "around 3pm"), use group_by=hour so a \
one-hour spike isn't averaged away, and report the worst hour.
- To explain a problem: find when it started (get_stats grouped by hour or day), then find \
what it is concentrated in. Deploys are the most common cause: if a release went out \
around the start, compare before vs after it with by_route=true to see which routes it \
broke, and break down by release; otherwise \
break down by host (always, for a problem on one route), then status_code or error_type. Name the cause only when the numbers \
show it, e.g. "v2 fails 35% vs 2.5% on v1". A release that was already running before \
the problem started is not its cause.
- Answer in at most 150 words: lead with the direct answer, then the evidence. Quote the \
numbers you used with their time range (error rates as percentages, latency in ms).
- Questions about who calls the API, a client or tenant, or who was affected go to \
get_consumers (and get_breakdown by consumer_id for a time range); questions about LLM \
spend or tokens go to get_llm_costs.
- Questions about the API surface (undocumented, unused or deprecated endpoints, the spec) \
go to get_api_drift.
- If the tools can't answer the question (outside retention, no such route, not about this \
service's traffic), say so plainly instead of answering anyway.
- Plain text: no markdown emphasis, tables or headings; short "- " bullets are fine."""


FINAL_ANSWER_NUDGE = (
    "Tool budget used up. Answer the question now from the tool results above; "
    "say what is missing if they aren't enough."
)


class AskError(Exception):
    """The model couldn't produce an answer (provider error, bad tool use)."""


def _client():
    from groq import AsyncGroq

    return AsyncGroq(api_key=settings.groq_api_key, timeout=30.0)


async def _system_prompt(pool: asyncpg.Pool, service: str, now: datetime) -> str:
    routes = await queries.list_routes(pool, service)
    shown = routes[:MAX_ROUTES_IN_PROMPT]
    route_text = ", ".join(shown) if shown else "(none yet)"
    if len(routes) > len(shown):
        route_text += f" and {len(routes) - len(shown)} more"
    releases = await _recent_releases(pool, service)
    release_text = ", ".join(
        f"{r['release']} ({r['first_seen_at']:%Y-%m-%d %H:%M})" for r in releases
    ) or "(none recorded)"
    sketches = await queries.sketches_available(pool)
    return SYSTEM_PROMPT.format(
        service=service,
        now=now.strftime("%Y-%m-%d %H:%M"),
        weekday=now.strftime("%A"),
        calendar=_calendar(now),
        aggregate_days=90 if sketches else 14,
        routes=route_text,
        releases=release_text,
        max_calls=MAX_TOOL_CALLS,
    )


def _calendar(now: datetime) -> str:
    """Dates of the last 8 days, so the model doesn't do date arithmetic."""
    days = [now - timedelta(days=i) for i in range(8)]
    labels = ["today", "yesterday"] + [""] * 6
    return "\n".join(
        f"- {d:%A}: {d:%Y-%m-%d}" + (f" ({label})" if label else "") for d, label in zip(days, labels)
    )


async def _recent_releases(pool: asyncpg.Pool, service: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT release, min(first_seen_at) AS first_seen_at
        FROM deployments WHERE service_name = $1
        GROUP BY release ORDER BY first_seen_at DESC LIMIT $2
        """,
        service, MAX_RELEASES_IN_PROMPT,
    )
    return [dict(r) for r in rows]


def _final_messages(opening: list[dict], steps: list[dict]) -> list[dict]:
    gathered = "\n".join(
        f"{s['tool']}({json.dumps(s['arguments'])}) -> {json.dumps(s['result'], default=str)}"
        for s in steps
    )
    return [
        *opening,
        {"role": "user", "content": f"Data gathered so far:\n{gathered}\n\n{FINAL_ANSWER_NUDGE}"},
    ]


async def _complete(client, messages: list[dict], allow_tools: bool):
    """One model turn. A malformed tool call (Groq's tool_use_failed) is
    retried once; anything else is an AskError."""
    kwargs = {"tools": TOOL_SCHEMAS, "tool_choice": "auto"} if allow_tools else {}
    for attempt in (1, 2):
        try:
            response = await client.chat.completions.create(
                model=settings.ask_model,
                messages=messages,
                temperature=0.1,
                max_tokens=2000,  # includes the model's reasoning tokens
                **kwargs,
            )
            return response.choices[0].message
        except Exception as exc:
            logger.warning("ask: model call failed (attempt %d): %s", attempt, exc)
            if attempt == 2 or "tool_use_failed" not in str(exc):
                raise AskError("the model call failed; try again or rephrase the question") from exc


# Typographic characters some models emit; normalized so answers can be
# searched and copied as typed.
_PLAIN = str.maketrans({"\u2018": "'", "\u2019": "'", "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u00a0": " ", "\u202f": " ", "\u2009": " "})


def _plain(text: str) -> str:
    return text.translate(_PLAIN).strip()


def _parse_arguments(raw: str | None) -> dict | str:
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return "arguments were not valid JSON"
    return args if isinstance(args, dict) else "arguments must be a JSON object"


async def ask(
    pool: asyncpg.Pool, service: str, question: str, now: datetime | None = None, client=None
) -> dict:
    """{"answer", "steps": [{"tool", "arguments", "result"}], "model"}."""
    now = now or datetime.now(timezone.utc)
    client = client or _client()
    messages: list[dict] = [
        {"role": "system", "content": await _system_prompt(pool, service, now)},
        {"role": "user", "content": question},
    ]
    steps: list[dict] = []

    for round_no in range(MAX_ROUNDS):
        allow_tools = len(steps) < MAX_TOOL_CALLS and round_no < MAX_ROUNDS - 1
        if not allow_tools:
            # Final answer without tools. Neither tool_choice="none" nor
            # dropping `tools` is enough: with tool calls in the history some
            # models call another one and the provider rejects the reply. So
            # the history is replaced by its results as plain text.
            messages = _final_messages(messages[:2], steps)
        message = await _complete(client, messages, allow_tools)
        calls = list(message.tool_calls or []) if allow_tools else []
        if not calls:
            answer = _plain(message.content or "")
            if not answer:
                raise AskError("the model returned an empty answer")
            return {
                "answer": answer,
                "steps": steps,
                "model": settings.ask_model,
                "unverified_numbers": unverified_numbers(
                    answer, steps, context=messages[0]["content"] + "\n" + question
                ),
            }

        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls
            ],
        })
        for call in calls:
            args = _parse_arguments(call.function.arguments)
            if len(steps) >= MAX_TOOL_CALLS:
                result = {"error": "tool call limit reached; answer with the data you have"}
            elif isinstance(args, str):
                result = {"error": args}
            else:
                result = await run_tool(pool, service, call.function.name, args, now)
            steps.append({
                "tool": call.function.name,
                "arguments": args if isinstance(args, dict) else {},
                "result": result,
            })
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, default=str),
            })

    raise AskError("the model did not finish within the step limit")
