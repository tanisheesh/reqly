from __future__ import annotations

import asyncio
import json
import logging

from ..config import settings

logger = logging.getLogger("reqly.collector")

# The LLM is given ONLY the structured, pre-computed anomaly JSON -- never
# raw events. Weekly batch job, so Groq's famous low-latency inference
# doesn't actually matter here; report quality is worth more than speed in
# this one spot, hence a 70b model rather than an instant/small one.
SYSTEM_PROMPT = """You are a site-reliability analyst. You will be given pre-computed \
statistical anomalies for an API service's past week. Write a concise report \
(3-6 bullet points) explaining what was observed.

Rules:
- Only reference the numbers given. Do not invent root causes you cannot verify from the data.
- Phrase causal explanations as hypotheses ("likely due to", "consistent with"), never as \
asserted fact, and note explicitly that any causal explanation is a hypothesis worth \
investigating, not a confirmed diagnosis.
- If the anomalies list is empty, state plainly that no significant anomalies were found \
this week. Do not invent a problem to seem useful.
"""


async def generate_report(service_name: str, week_start: str, anomalies: list[dict]) -> str:
    if not anomalies:
        return (
            f"No significant anomalies were found for **{service_name}** "
            f"for the week of {week_start}."
        )

    if not settings.groq_api_key:
        return _fallback_report(service_name, week_start, anomalies)

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _call_groq_sync, service_name, week_start, anomalies
        )
    except Exception:
        logger.exception("groq report generation failed, falling back to raw summary")
        return _fallback_report(service_name, week_start, anomalies)


def _call_groq_sync(service_name: str, week_start: str, anomalies: list[dict]) -> str:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key, timeout=30.0)
    payload = {"service_name": service_name, "week_of": week_start, "anomalies": anomalies}
    response = client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
        temperature=0.3,
        max_tokens=600,
    )
    return response.choices[0].message.content


def _fallback_report(service_name: str, week_start: str, anomalies: list[dict]) -> str:
    """Used when GROQ_API_KEY isn't configured, or the Groq call fails --
    keeps the panel useful (the real statistical findings are still shown)
    instead of erroring out the whole insights feature.
    """
    lines = [
        f"AI Insights for **{service_name}**, week of {week_start} "
        "(raw statistical findings -- GROQ_API_KEY not configured or call failed):"
    ]
    for a in anomalies:
        lines.append(
            f"- `{a['route']}` on {a['day_of_week']} {a['hour_range']}: "
            f"error rate {a['observed_error_rate']:.1%} vs baseline "
            f"{a['baseline_error_rate']:.1%}; p95 {a['observed_p95_ms']}ms vs baseline "
            f"{a['baseline_p95_ms']}ms (z-score {a['z_score']})"
        )
    return "\n".join(lines)
