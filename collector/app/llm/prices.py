"""LLM price table: llm_prices.yaml next to this file, overridden and
extended by the file in LLM_PRICES_FILE (if set)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("reqly.collector")

DEFAULT_PRICES = Path(__file__).with_name("llm_prices.yaml")
PER_TOKENS = 1_000_000


@dataclass(frozen=True)
class Price:
    input: float  # USD per 1M input tokens
    output: float  # USD per 1M output tokens

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input + output_tokens * self.output) / PER_TOKENS


class PriceTable:
    def __init__(self, prices: dict[str, Price], as_of: str | None = None) -> None:
        self.prices = {k.lower(): v for k, v in prices.items()}
        self.as_of = as_of
        # longest first, so "gpt-4o-mini" wins over "gpt-4o"
        self._keys = sorted(self.prices, key=len, reverse=True)

    def lookup(self, model: str | None) -> tuple[str, Price] | None:
        """(matched table key, price), or None for an unpriced model."""
        if not model:
            return None
        name = model.strip().lower()
        candidates = [name]
        if "/" in name:  # "openai/gpt-4o", "anthropic/claude-sonnet-4"
            candidates.append(name.rsplit("/", 1)[1])
        for candidate in candidates:
            for key in self._keys:
                if candidate.startswith(key):
                    return key, self.prices[key]
        return None


def _read(path: Path) -> tuple[dict[str, Price], str | None]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    prices = {}
    for model, entry in (data.get("models") or {}).items():
        try:
            prices[str(model)] = Price(input=float(entry["input"]), output=float(entry["output"]))
        except (KeyError, TypeError, ValueError):
            logger.warning("LLM prices: ignoring malformed entry %r in %s", model, path)
    as_of = data.get("as_of")
    return prices, str(as_of) if as_of is not None else None


@lru_cache(maxsize=1)
def price_table() -> PriceTable:
    prices, as_of = _read(DEFAULT_PRICES)
    override = os.environ.get("LLM_PRICES_FILE")
    if override:
        try:
            extra, extra_as_of = _read(Path(override))
            prices.update(extra)
            as_of = extra_as_of or as_of
        except Exception:
            logger.exception("LLM prices: could not read LLM_PRICES_FILE=%s, using defaults", override)
    return PriceTable(prices, as_of)
