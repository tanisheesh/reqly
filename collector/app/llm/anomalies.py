"""LLM cost anomalies: a route's LLM spend in one hour against the same
weekday-hour in earlier weeks.

Cost is not count data, so the error-rate test doesn't apply. Instead each
route's hour is compared with the median of its baseline hours, with a robust
spread (MAD), and a change has to clear an absolute floor in dollars so cheap
routes don't page anyone over cents.

Two causes are told apart, because they need different fixes:

- unit_cost: the same traffic costs more per request (longer prompts, more
  output, a pricier model, retries) -- the usual "someone shipped a prompt
  change" case.
- volume: cost per request is normal but there are many more requests (a
  client loop, a bot, a launch).

Each finding carries "drivers": which of requests, the share of requests that
call a model, tokens per call and the blended price per token went up, so the
alert says why, not just how much.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .prices import PriceTable

MIN_BASELINE_SAMPLES = 3
UNIT_COST_RATIO = 2.0  # cost per request at least 2x the usual
VOLUME_RATIO = 3.0  # at least 3x the usual requests
ROBUST_Z = 4.0
DRIVER_MIN_CHANGE = 1.25  # report factors that went up by 25%+


@dataclass
class _Hour:
    requests: int = 0
    llm_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    model_cost: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def unit_cost(self) -> float:
        return self.cost / self.requests if self.requests else 0.0

    @property
    def llm_share(self) -> float:
        return self.llm_requests / self.requests if self.requests else 0.0

    @property
    def tokens_per_call(self) -> float:
        return self.tokens / self.llm_requests if self.llm_requests else 0.0

    @property
    def usd_per_1m_tokens(self) -> float:
        return self.cost * 1_000_000 / self.tokens if self.tokens else 0.0


def _hours_by_route(rows, prices: PriceTable) -> dict[str, dict[datetime, _Hour]]:
    """rows: (bucket, route, llm_model, request_count, input_tokens, output_tokens),
    one per model; llm_model None is the route's requests that called no model."""
    out: dict[str, dict[datetime, _Hour]] = defaultdict(lambda: defaultdict(_Hour))
    for r in rows:
        hour = out[r["route"]][r["bucket"]]
        requests = int(r["request_count"])
        hour.requests += requests
        model = r["llm_model"]
        if model is None:
            continue
        tokens_in, tokens_out = int(r["input_tokens"] or 0), int(r["output_tokens"] or 0)
        hour.llm_requests += requests
        hour.input_tokens += tokens_in
        hour.output_tokens += tokens_out
        match = prices.lookup(model)
        if match is not None:  # unpriced models add tokens but no cost
            cost = match[1].cost(tokens_in, tokens_out)
            hour.cost += cost
            hour.model_cost[model] += cost
    return out


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _mad(values: list[float], center: float) -> float:
    """Median absolute deviation, scaled to be comparable to a std dev."""
    return 1.4826 * _median([abs(v - center) for v in values]) if values else 0.0


def _ratio(after: float, before: float) -> float | None:
    if before <= 0:
        return None if after <= 0 else math.inf
    return after / before


def _fmt_ratio(ratio: float | None) -> str:
    if ratio is None:
        return "—"
    if math.isinf(ratio):
        return "new"
    return f"{ratio:.1f}×"


def _drivers(current: _Hour, baseline: list[_Hour]) -> list[dict]:
    factors = [
        ("requests", "requests", current.requests, _median([h.requests for h in baseline]), "{:,.0f}"),
        ("llm_share", "share of requests calling a model", current.llm_share,
         _median([h.llm_share for h in baseline]), "{:.0%}"),
        ("tokens_per_call", "tokens per model call", current.tokens_per_call,
         _median([h.tokens_per_call for h in baseline if h.llm_requests]), "{:,.0f}"),
        ("usd_per_1m_tokens", "price per 1M tokens", current.usd_per_1m_tokens,
         _median([h.usd_per_1m_tokens for h in baseline if h.tokens]), "${:,.2f}"),
    ]
    drivers = []
    for key, label, after, before, fmt in factors:
        ratio = _ratio(after, before)
        # Only factors that went up explain a rise in cost; one that fell
        # (e.g. the blended price per token, when cheap input tokens grew)
        # is a side effect, not a cause.
        if ratio is None or ratio < DRIVER_MIN_CHANGE:
            continue
        drivers.append({
            "factor": key,
            "before": round(before, 6),
            "after": round(after, 6),
            "ratio": None if math.isinf(ratio) else round(ratio, 2),
            "text": f"{label} {_fmt_ratio(ratio)} ({fmt.format(before)} → {fmt.format(after)})",
        })
    drivers.sort(key=lambda d: -d["ratio"] if d["ratio"] else -math.inf)  # biggest rise first
    return drivers


def _model_mix(current: _Hour, baseline: list[_Hour]) -> dict | None:
    """The model that carries most of the cost now, if it isn't the one that
    usually did."""
    if not current.model_cost or current.cost <= 0:
        return None
    top_now, cost_now = max(current.model_cost.items(), key=lambda kv: kv[1])
    usual: dict[str, float] = defaultdict(float)
    for h in baseline:
        for model, cost in h.model_cost.items():
            if cost > 0:
                usual[model] += cost
    top_usual = max(usual.items(), key=lambda kv: kv[1])[0] if usual else None
    if top_usual == top_now:
        return None
    share = cost_now / current.cost
    text = f"{share:.0%} of the cost is on `{top_now}`"
    text += f" (usually `{top_usual}`)" if top_usual else " (new model)"
    return {"model": top_now, "share": round(share, 3), "usual_model": top_usual, "text": text}


def detect_cost_anomalies(
    rows,
    hour: datetime,
    prices: PriceTable,
    min_usd: float,
    min_samples: int = MIN_BASELINE_SAMPLES,
) -> list[dict]:
    """Pure. rows cover `hour` and the same weekday-hour in earlier weeks.
    Returns one finding per route whose LLM cost in `hour` is anomalous,
    biggest extra cost first."""
    findings = []
    for route, hours in _hours_by_route(rows, prices).items():
        current = hours.get(hour)
        if current is None or current.cost <= 0:
            continue
        # baseline hours where the route existed (got any request at all)
        baseline = [h for b, h in hours.items() if b != hour and h.requests > 0]
        if len(baseline) < min_samples:
            continue

        units = [h.unit_cost for h in baseline]
        base_unit = _median(units)
        spread = _mad(units, base_unit)
        base_cost = _median([h.cost for h in baseline])
        base_requests = _median([h.requests for h in baseline])

        expected = current.requests * base_unit  # this hour's traffic at the usual cost per request
        unit_extra = current.cost - expected
        volume_extra = expected - base_cost
        total_extra = current.cost - base_cost
        if total_extra < min_usd:
            continue

        unit_jump = base_unit == 0 or current.unit_cost >= UNIT_COST_RATIO * base_unit
        unit_z = None if spread == 0 else (current.unit_cost - base_unit) / spread
        unit_anomaly = unit_extra >= min_usd and unit_jump and (unit_z is None or unit_z >= ROBUST_Z)
        volume_anomaly = volume_extra >= min_usd and current.requests >= VOLUME_RATIO * max(base_requests, 1)
        if not (unit_anomaly or volume_anomaly):
            continue

        cause = "unit_cost" if unit_anomaly and (not volume_anomaly or unit_extra >= volume_extra) else "volume"
        mix = _model_mix(current, baseline)
        # factors of the cause first: per-request ones for a unit-cost spike,
        # the request count for a volume spike
        drivers = sorted(_drivers(current, baseline), key=lambda d: (d["factor"] == "requests") == (cause == "unit_cost"))
        findings.append({
            "kind": "llm_cost",
            "route": route,
            "cause": cause,
            "window_start": hour.isoformat(),
            "day_of_week": hour.strftime("%A"),
            "hour_range": f"{hour:%H:%M}-{hour + timedelta(hours=1):%H:%M}",
            "observed_cost_usd": round(current.cost, 4),
            "baseline_cost_usd": round(base_cost, 4),
            "extra_cost_usd": round(total_extra, 4),
            "observed_cost_per_1k_requests": round(1000 * current.unit_cost, 4),
            "baseline_cost_per_1k_requests": round(1000 * base_unit, 4),
            "requests": current.requests,
            "baseline_requests": round(base_requests, 1),
            "robust_z": None if unit_z is None else round(unit_z, 2),
            "baseline_samples": len(baseline),
            "drivers": drivers,
            "model_mix": mix,
        })
    findings.sort(key=lambda f: -f["extra_cost_usd"])
    return findings
