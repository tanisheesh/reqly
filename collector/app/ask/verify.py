"""Checks an Ask Reqly answer's numbers against the tool results.

The model is told to quote only numbers it got from tools, but LLMs still
mis-copy digits now and then ("4 778 errors" for 478). Every number in the
answer is looked up in the results -- as written, as a percentage of a
fraction, or as seconds of a millisecond value, at the precision written --
and the ones that can't be found are returned, so the dashboard can flag
them instead of presenting them as fact.
"""

from __future__ import annotations

import re

# Dates, clock times and ISO timestamps are checked by eye, not here.
_DATE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?|\b\d{1,2}:\d{2}(?::\d{2})?\b"
)
# A number not glued to a word, path or version ("p95", "v2", "pod-3",
# "/v1", "2026.10.0"); thousands may be grouped with "," or a space.
_NUMBER = re.compile(r"(?<![\w./-])(\d{1,3}(?:[, ]\d{3})+|\d+)(\.\d+)?(?![\w/-]|\.\d)")
# Small bare integers are mostly counts of things in the question ("last 7
# days", "top 3"), not data.
_MIN_BARE_INTEGER = 32


def _result_numbers(value, out: list[float]) -> list[float]:
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        out.append(abs(float(value)))  # the answer writes "-4.85%" or "down 4.85%"
    elif isinstance(value, str):
        # breakdown values such as status codes ("503") are strings
        try:
            out.append(abs(float(value)))
        except ValueError:
            pass
    elif isinstance(value, dict):
        for v in value.values():
            _result_numbers(v, out)
    elif isinstance(value, list):
        for v in value:
            _result_numbers(v, out)
    return out


def _matches(written: float, decimals: int, value: float) -> bool:
    step = 10 ** -decimals
    # as written, a fraction as a percentage, ms as seconds, and a ratio as a
    # percent change ("x1.68" -> "+68%")
    candidates = [value, value * 100, value / 1000]
    if 0 < value < 10:
        candidates.append(abs(value - 1) * 100)
    for candidate in candidates:
        # at the precision the answer uses, allowing for either rounding
        # direction -- or within 1%, for numbers written loosely
        if abs(candidate - written) <= step or abs(candidate - written) <= 0.01 * abs(candidate):
            return True
    return False


def _text_numbers(text: str) -> list[float]:
    return [float(re.sub(r"[, ]", "", m.group(1)) + (m.group(2) or "")) for m in _NUMBER.finditer(text)]


def unverified_numbers(answer: str, steps: list[dict], context: str = "") -> list[str]:
    """`context` is text the model was given besides tool results (the
    question, the system prompt): numbers from it count as known."""
    known = _result_numbers([s.get("result") for s in steps], []) + _text_numbers(context)
    text = _DATE_TIME.sub(" ", answer)
    unverified = []
    for match in _NUMBER.finditer(text):
        whole, fraction = match.group(1), match.group(2) or ""
        written = float(re.sub(r"[, ]", "", whole) + fraction)
        decimals = len(fraction) - 1 if fraction else 0
        follows = text[match.end():match.end() + 2].lstrip()
        if not fraction and written < _MIN_BARE_INTEGER and not follows.startswith("%"):
            continue
        if not any(_matches(written, decimals, v) for v in known):
            unverified.append(match.group(0))
    return list(dict.fromkeys(unverified))
