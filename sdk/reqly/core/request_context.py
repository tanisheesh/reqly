"""Per-request state the integrations collect besides timing: who the
caller is (consumer) and what LLM usage the request caused.

LLM usage is recorded from inside the request handler with
``reqly.record_llm_usage()``, so it lives in a ContextVar the middleware
sets when the request starts. The variable holds a mutable accumulator, so
usage recorded in a thread-pool copy of the context (Starlette runs sync
endpoints that way) still lands on the request's accumulator.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from collections.abc import Mapping as _MappingABC
from typing import Any, Callable, Iterable, Mapping, Optional

logger = logging.getLogger("reqly")

_CONSUMER_HASH_HEX = 16
_HASH_CACHE_SIZE = 4096
_MAX_CONSUMER_LEN = 128
_MAX_MODEL_LEN = 255


class LazyHeaders(_MappingABC):
    """Request headers as a read-only mapping with lower-case names, read
    on demand: the consumer header is one lookup, and the full set is only
    collected if something iterates it (a custom consumer= function)."""

    __slots__ = ("_get_one", "_items", "_all")

    def __init__(self, get_one: Callable[[str], Optional[str]], items: Callable[[], Iterable[tuple]]) -> None:
        self._get_one = get_one
        self._items = items
        self._all: dict | None = None

    def _materialize(self) -> dict:
        if self._all is None:
            self._all = {str(k).lower(): v for k, v in self._items()}
        return self._all

    def __getitem__(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise KeyError(name)
        return value

    def get(self, name: str, default=None):
        if self._all is not None:
            return self._all.get(name.lower(), default)
        value = self._get_one(name.lower())
        return default if value is None else value

    def __iter__(self):
        return iter(self._materialize())

    def __len__(self) -> int:
        return len(self._materialize())


@dataclass
class RequestInfo:
    """What a ``consumer=`` callable receives: the method, path, headers
    (lower-case names) and the framework's own request object as ``raw``
    (an ASGI scope, a Flask/Django request or a WSGI environ)."""

    method: str
    path: str
    headers: Mapping[str, str]
    raw: Any = None


# --- LLM usage -----------------------------------------------------------------


@dataclass
class LLMUsage:
    tokens_by_model: dict = field(default_factory=dict)  # model -> [input, output]

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        totals = self.tokens_by_model.setdefault(model, [0, 0])
        totals[0] += input_tokens
        totals[1] += output_tokens

    def summary(self) -> tuple[str, int, int] | None:
        """(model, input_tokens, output_tokens) for the event. A request
        that used several models is attributed to the one with the most
        tokens and carries the token totals of all of them."""
        if not self.tokens_by_model:
            return None
        model = max(self.tokens_by_model, key=lambda m: sum(self.tokens_by_model[m]))
        input_total = sum(t[0] for t in self.tokens_by_model.values())
        output_total = sum(t[1] for t in self.tokens_by_model.values())
        return model, input_total, output_total


_current_usage: contextvars.ContextVar[Optional[LLMUsage]] = contextvars.ContextVar(
    "reqly_llm_usage", default=None
)


def begin_request() -> contextvars.Token:
    return _current_usage.set(LLMUsage())


def end_request(token: contextvars.Token | None) -> tuple[str, int, int] | None:
    usage = _current_usage.get()
    if token is not None:
        try:
            _current_usage.reset(token)
        except ValueError:  # token from another context; nothing to restore
            _current_usage.set(None)
    return usage.summary() if usage is not None else None


def _as_count(value) -> int:
    count = int(value or 0)
    if count < 0:
        raise ValueError("token counts can't be negative")
    return count


def record_llm_usage(model: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Attribute LLM token usage to the request being served. Call it after
    each model call inside a request handler; calls add up. Outside an
    instrumented request it does nothing. Never raises."""
    try:
        usage = _current_usage.get()
        if usage is None:
            logger.debug("reqly: record_llm_usage() outside an instrumented request, ignored")
            return
        usage.add(str(model)[:_MAX_MODEL_LEN], _as_count(input_tokens), _as_count(output_tokens))
    except Exception:
        logger.warning("reqly: record_llm_usage() failed", exc_info=True)


def _field(obj, name):
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def record_llm_response(response) -> None:
    """``record_llm_usage()`` from a provider response object or dict:
    OpenAI-style (``usage.prompt_tokens`` / ``completion_tokens``, also
    Groq, Mistral, vLLM, LiteLLM...), OpenAI Responses API and Anthropic
    (``usage.input_tokens`` / ``output_tokens``). Never raises."""
    try:
        usage = _field(response, "usage")
        if usage is None:
            return
        input_tokens = _field(usage, "prompt_tokens")
        output_tokens = _field(usage, "completion_tokens")
        if input_tokens is None and output_tokens is None:
            input_tokens = _field(usage, "input_tokens")
            output_tokens = _field(usage, "output_tokens")
        record_llm_usage(_field(response, "model") or "unknown", input_tokens or 0, output_tokens or 0)
    except Exception:
        logger.warning("reqly: record_llm_response() failed", exc_info=True)


# --- consumers -----------------------------------------------------------------

ConsumerCallable = Callable[[RequestInfo], Optional[str]]


class ConsumerResolver:
    """Turns a request into a consumer id: from a header or a callable,
    then (by default) HMAC-SHA256 with the app's salt, truncated -- the raw
    API key or user id never leaves the app."""

    def __init__(
        self,
        *,
        header: str | None,
        func: ConsumerCallable | None,
        salt: str | None,
        hash_ids: bool,
    ) -> None:
        self._header = header.lower() if header else None
        self._func = func
        self._salt = (salt or "").encode()
        self._hash = hash_ids
        # The same few API keys / tenants call over and over: hash each once.
        self._hashed: dict[str, str] = {}
        if hash_ids and not salt:
            logger.warning(
                "reqly: consumer tracking without REQLY_CONSUMER_SALT -- hashed ids of guessable "
                "values (user ids, emails) can be reversed by trying candidates; set a secret salt"
            )

    @property
    def enabled(self) -> bool:
        return bool(self._header or self._func)

    def resolve(self, info_factory: Callable[[], RequestInfo]) -> str | None:
        info = info_factory()
        value = self._func(info) if self._func is not None else info.headers.get(self._header)
        if value is None or value == "":
            return None
        value = str(value)
        if not self._hash:
            return value[:_MAX_CONSUMER_LEN]
        hashed = self._hashed.get(value)
        if hashed is None:
            if len(self._hashed) >= _HASH_CACHE_SIZE:
                self._hashed.clear()  # bounded; refilled by the busy consumers
            hashed = hmac.new(self._salt, value.encode(), hashlib.sha256).hexdigest()[:_CONSUMER_HASH_HEX]
            self._hashed[value] = hashed
        return hashed
