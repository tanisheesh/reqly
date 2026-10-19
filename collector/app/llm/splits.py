"""Per-model token splits for requests that call several LLM models (see
migrations/011_llm_usage_splits.sql for why they are deltas)."""

from __future__ import annotations

from datetime import datetime

MAX_MODELS_PER_REQUEST = 16


def summarize_models(usage: list[tuple[str, int, int]]) -> tuple[str, int, int, list[tuple[str, int, int]]]:
    """usage: (model, input, output) per call or per model, in any order.
    Returns the request's main model (most tokens; the first on a tie), its
    token totals, and each model's own tokens -- main model first."""
    merged: dict[str, list[int]] = {}
    for model, tokens_in, tokens_out in usage:
        totals = merged.setdefault(model, [0, 0])
        totals[0] += tokens_in
        totals[1] += tokens_out
    ranked = sorted(merged.items(), key=lambda item: -sum(item[1]))  # stable: ties keep order
    per_model = [(model, t[0], t[1]) for model, t in ranked]
    return per_model[0][0], sum(t[1] for t in per_model), sum(t[2] for t in per_model), per_model


def split_rows(
    event_id: str, time: datetime, service_name: str, route: str, per_model: list[tuple[str, int, int]]
) -> list[tuple]:
    """llm_usage_splits rows for one request (none for a single model): the
    main model gives up the other models' tokens, which each get their own.
    per_model: summarize_models() output, main model first."""
    if len(per_model) < 2:
        return []
    main, *others = per_model
    moved_in = sum(m[1] for m in others)
    moved_out = sum(m[2] for m in others)
    rows = [(time, event_id, service_name, route, main[0], -moved_in, -moved_out)]
    rows += [(time, event_id, service_name, route, model, tokens_in, tokens_out) for model, tokens_in, tokens_out in others]
    return rows
