from __future__ import annotations

import asyncio
import json
import logging

from ..config import settings
from .report import SYSTEM_PROMPT, fallback_report

logger = logging.getLogger("reqly.collector")


async def generate_report(service_name: str, week_start: str, anomalies: list[dict]) -> str:
    if not anomalies:
        return (
            f"No significant anomalies were found for **{service_name}** "
            f"for the week of {week_start}."
        )

    if not settings.groq_api_key:
        return fallback_report(service_name, week_start, anomalies)

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _call_groq_sync, service_name, week_start, anomalies
        )
    except Exception:
        logger.exception("groq report generation failed, falling back to raw summary")
        return fallback_report(service_name, week_start, anomalies)


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
        max_tokens=2000,  # includes the model's reasoning tokens
    )
    return response.choices[0].message.content
