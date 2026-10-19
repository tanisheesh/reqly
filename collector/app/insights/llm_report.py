from __future__ import annotations

import json
import logging

from ..config import settings
from ..llm.client import llm_client
from .report import SYSTEM_PROMPT, fallback_report

logger = logging.getLogger("reqly.collector")


async def generate_report(
    service_name: str, week_start: str, anomalies: list[dict], drift: dict | None = None
) -> str:
    """drift: a summarize_drift() result, or None (no spec, or nothing to say)."""
    if not anomalies and not drift:
        return (
            f"No significant anomalies were found for **{service_name}** "
            f"for the week of {week_start}."
        )

    if not settings.llm_enabled:
        return fallback_report(service_name, week_start, anomalies, drift)

    try:
        return await _write_report(service_name, week_start, anomalies, drift)
    except Exception:
        logger.exception("LLM report generation failed, falling back to raw summary")
        return fallback_report(service_name, week_start, anomalies, drift)


async def _write_report(service_name: str, week_start: str, anomalies: list[dict], drift: dict | None) -> str:
    payload = {"service_name": service_name, "week_of": week_start, "anomalies": anomalies}
    if drift:
        payload["api_drift"] = drift
    response = await llm_client().chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
        temperature=0.3,
        max_tokens=2000,  # includes the model's reasoning tokens
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("the model returned an empty report")
    return content
