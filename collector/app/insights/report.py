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
- If an anomaly has "hints", they are measured concentrations (for example most errors \
coming from one host, or a new error type). Use them as leads, still phrased as hypotheses.
- If the anomalies list is empty, state plainly that no significant anomalies were found \
this week. Do not invent a problem to seem useful.
- If "api_drift" is present, add one bullet about the API surface: how much traffic went to \
undocumented endpoints, documented endpoints nobody called, and deprecated endpoints still \
being called (and by which consumers). Report only the numbers given.
"""

DRIFT_TOP = 5


def summarize_drift(drift: dict | None) -> dict | None:
    """The part of an OpenAPI drift report worth a line in the weekly report:
    counts plus the top few items of each list. None without a spec, or when
    the spec and the traffic agree."""
    if not drift:
        return None
    total = drift.get("total_requests") or 0
    summary = {
        "window_days": drift.get("window_days"),
        "coverage": drift.get("coverage"),
        "undocumented_traffic_share": round(drift.get("undocumented_requests", 0) / total, 4) if total else 0.0,
        "undocumented_count": len(drift.get("undocumented") or []),
        "unused_count": len(drift.get("dead") or []),
        "deprecated_in_use_count": len(drift.get("deprecated_in_use") or []),
        "top_undocumented": [
            {"method": d["method"], "route": d["route"], "requests": d["requests"]}
            for d in (drift.get("undocumented") or [])[:DRIFT_TOP]
        ],
        "top_unused": [
            {"method": d["method"], "path": d["path"]} for d in (drift.get("dead") or [])[:DRIFT_TOP]
        ],
        "deprecated_in_use": [
            {
                "method": d["method"],
                "path": d["path"],
                "requests": d["requests"],
                "consumers": [c["consumer_id"] for c in (d.get("consumers") or [])[:3]],
            }
            for d in (drift.get("deprecated_in_use") or [])[:DRIFT_TOP]
        ],
    }
    if not (summary["undocumented_count"] or summary["unused_count"] or summary["deprecated_in_use_count"]):
        return None
    return summary


def drift_lines(drift: dict) -> list[str]:
    """Plain-text lines for a summarize_drift() result."""
    lines = [
        f"- API surface (last {drift['window_days']} days): "
        f"{drift['undocumented_count']} undocumented endpoint(s) with traffic "
        f"({drift['undocumented_traffic_share']:.1%} of requests), "
        f"{drift['unused_count']} documented endpoint(s) never called, "
        f"{drift['deprecated_in_use_count']} deprecated endpoint(s) still called"
    ]
    for d in drift["top_undocumented"][:3]:
        lines.append(f"  - undocumented: `{d['method']} {d['route']}` ({d['requests']} requests)")
    for d in drift["deprecated_in_use"][:3]:
        who = f" by {', '.join(f'`{c}`' for c in d['consumers'])}" if d["consumers"] else ""
        lines.append(f"  - deprecated but called: `{d['method']} {d['path']}` ({d['requests']} requests{who})")
    return lines


def fallback_report(service_name: str, week_start: str, anomalies: list[dict], drift: dict | None = None) -> str:
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
        for hint in a.get("hints") or []:
            line += f"; {hint['text']}"
        lines.append(line)
    if not anomalies:
        lines.append("- No significant anomalies this week.")
    if drift:
        lines.extend(drift_lines(drift))
    return "\n".join(lines)
