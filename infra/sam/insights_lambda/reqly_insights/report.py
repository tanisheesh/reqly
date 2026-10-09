"""LLM prompt and plain-text fallback for the insights report.

Pure module (no app config, no I/O) so the SAM Lambda can vendor it as-is;
see infra/sam/sync_insights.py.
"""

from __future__ import annotations

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
- If an anomaly has a "release_context" whose "is_new_release" is true, the regression \
started while a release first seen shortly before it was running. Mention that release and \
its before/after numbers as a likely lead, still phrased as a hypothesis.
- If the anomalies list is empty, state plainly that no significant anomalies were found \
this week. Do not invent a problem to seem useful.
"""


def fallback_report(service_name: str, week_start: str, anomalies: list[dict]) -> str:
    """Used when no LLM key is configured, or the LLM call fails -- keeps the
    panel useful (the real statistical findings are still shown) instead of
    erroring out the whole insights feature.
    """
    lines = [
        f"AI Insights for **{service_name}**, week of {week_start} "
        "(raw statistical findings -- GROQ_API_KEY not configured or call failed):"
    ]
    for a in anomalies:
        line = (
            f"- `{a['route']}` on {a['day_of_week']} {a['hour_range']}: "
            f"error rate {a['observed_error_rate']:.1%} vs baseline "
            f"{a['baseline_error_rate']:.1%}; p95 {a['observed_p95_ms']}ms vs baseline "
            f"{a['baseline_p95_ms']}ms (z-score {a['z_score']})"
        )
        context = a.get("release_context") or {}
        if context.get("is_new_release"):
            line += (
                f" -- running release `{context['release']}`, first seen "
                f"{context['release_first_seen_at']}"
            )
            if context.get("previous_release"):
                line += f" (previous: `{context['previous_release']}`)"
        lines.append(line)
    return "\n".join(lines)
