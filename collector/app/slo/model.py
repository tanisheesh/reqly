"""Error-budget and burn-rate math for SLOs (pure, no I/O).

Follows the multi-window, multi-burn-rate approach from the Google SRE
workbook ("Alerting on SLOs"):

- burn rate = (bad / total in a window) / (1 - target). 1.0 spends exactly
  the whole budget over the SLO window; 14.4 spends 2% of a 30-day budget
  in one hour.
- fast burn: the 1h AND 5m burn rates are both >= 14.4 -- severe and still
  happening (the short window makes the alert stop soon after recovery).
- slow burn: the 6h AND 30m burn rates are both >= 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

FAST_BURN_RATE = 14.4
SLOW_BURN_RATE = 6.0
# Below this many requests in the longer window of a pair, a burn rate is
# noise (3 errors in 10 requests is a 30x burn on a 99% SLO).
MIN_REQUESTS_FOR_BURN = 20

SHORT_WINDOWS: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
}

OK = "ok"
NO_DATA = "no_data"
FAST_BURN = "fast_burn"
SLOW_BURN = "slow_burn"
BUDGET_EXHAUSTED = "budget_exhausted"
BURNING_STATES = (FAST_BURN, SLOW_BURN)


@dataclass(frozen=True)
class Counts:
    bad: float
    total: int


def burn_rate(counts: Counts, target: float) -> float:
    if counts.total <= 0:
        return 0.0
    return (counts.bad / counts.total) / (1 - target)


def evaluate(target: float, window: Counts, short: dict[str, Counts]) -> dict:
    """Status of one SLO from its counts over the SLO window and the short
    alerting windows (keys of SHORT_WINDOWS)."""
    burns = {name: round(burn_rate(short[name], target), 2) for name in SHORT_WINDOWS}

    if window.total <= 0:
        return {"state": NO_DATA, "sli": None, "total": 0, "bad": 0,
                "budget_remaining": None, "burn_rates": burns}

    sli = 1 - window.bad / window.total
    allowed_bad = (1 - target) * window.total
    budget_remaining = 1 - window.bad / allowed_bad

    def burning(long_name: str, short_name: str, threshold: float) -> bool:
        return (
            short[long_name].total >= MIN_REQUESTS_FOR_BURN
            and burns[long_name] >= threshold
            and burns[short_name] >= threshold
        )

    if burning("1h", "5m", FAST_BURN_RATE):
        state = FAST_BURN
    elif burning("6h", "30m", SLOW_BURN_RATE):
        state = SLOW_BURN
    elif budget_remaining < 0:
        state = BUDGET_EXHAUSTED
    else:
        state = OK

    return {
        "state": state,
        "sli": round(sli, 6),
        "total": window.total,
        "bad": round(window.bad, 1),
        "budget_remaining": round(budget_remaining, 4),
        "burn_rates": burns,
    }
