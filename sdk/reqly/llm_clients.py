"""Token usage from LLM clients without ``record_llm_usage()`` calls::

    client = reqly.instrument_openai(OpenAI())       # or AsyncOpenAI()
    client = reqly.instrument_anthropic(Anthropic())  # or AsyncAnthropic()

Patches that one client object (never the classes): its create methods --
OpenAI chat completions, Responses, completions and embeddings; Anthropic
messages -- add each call's tokens to the request being served. Streams
count when they finish; OpenAI chat streams only report usage with
``stream_options={"include_usage": True}``. Works with any client shaped
like these (Groq, Mistral, vLLM and LiteLLM's OpenAI-compatible clients).
Never raises into the app: a response it can't read is skipped.
"""

from __future__ import annotations

import functools
import inspect
import logging

from .core.request_context import LLMUsage, _as_count, _field, current_usage, usage_from_response

logger = logging.getLogger("reqly")

_OPENAI_METHODS = (("chat", "completions"), ("responses",), ("completions",), ("embeddings",))
_ANTHROPIC_METHODS = (("messages",),)
_PATCHED = "_reqly_instrumented"


def _add(usage: LLMUsage | None, found) -> None:
    if usage is not None and found is not None:
        model, tokens_in, tokens_out = found
        usage.add(model[:255], tokens_in, tokens_out)


class _StreamTally:
    """Usage seen in a stream's chunks: OpenAI's final chunk (with
    include_usage), Responses' response.completed, Anthropic's message_start
    / message_delta."""

    def __init__(self) -> None:
        self.model: str | None = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.seen = False

    def observe(self, chunk) -> None:
        kind = _field(chunk, "type")
        if kind == "message_start":  # Anthropic
            message = _field(chunk, "message")
            self.model = _field(message, "model") or self.model
            usage = _field(message, "usage")
            self.input_tokens = _as_count(_field(usage, "input_tokens"))
            self.output_tokens = _as_count(_field(usage, "output_tokens"))
            self.seen = True
        elif kind == "message_delta":  # Anthropic: output so far, cumulative
            output = _field(_field(chunk, "usage"), "output_tokens")
            if output is not None:
                self.output_tokens = _as_count(output)
                self.seen = True
        elif kind == "response.completed":  # OpenAI Responses
            self._take(usage_from_response(_field(chunk, "response")))
        elif _field(chunk, "usage") is not None:  # OpenAI chat, include_usage
            self._take(usage_from_response(chunk))
        if self.model is None and _field(chunk, "model"):
            self.model = str(_field(chunk, "model"))

    def _take(self, found) -> None:
        if found is not None:
            self.model, self.input_tokens, self.output_tokens = found
            self.seen = True

    def result(self):
        return (self.model or "unknown", self.input_tokens, self.output_tokens) if self.seen else None


class _Stream:
    """Passes a stream through unchanged, adding its usage once it ends."""

    def __init__(self, stream, usage: LLMUsage | None) -> None:
        self._stream = stream
        self._usage = usage
        self._tally = _StreamTally()
        self._done = False

    def _finish(self) -> None:
        if not self._done:
            self._done = True
            try:
                _add(self._usage, self._tally.result())
            except Exception:
                logger.debug("reqly: couldn't read a stream's usage", exc_info=True)

    def _observe(self, chunk) -> None:
        try:
            self._tally.observe(chunk)
        except Exception:
            logger.debug("reqly: couldn't read a stream chunk", exc_info=True)

    def __iter__(self):
        try:
            for chunk in self._stream:
                self._observe(chunk)
                yield chunk
        finally:
            self._finish()

    async def __aiter__(self):
        try:
            async for chunk in self._stream:
                self._observe(chunk)
                yield chunk
        finally:
            self._finish()

    def __enter__(self):
        self._stream.__enter__()
        return self

    def __exit__(self, *exc):
        return self._stream.__exit__(*exc)

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._stream.__aexit__(*exc)

    def __getattr__(self, name):  # .response, .close(), ...
        return getattr(self._stream, name)


def _observe(result, streaming: bool, usage: LLMUsage | None):
    if streaming:
        return _Stream(result, usage)
    try:
        _add(usage, usage_from_response(result))
    except Exception:
        logger.debug("reqly: couldn't read an LLM response's usage", exc_info=True)
    return result


def _wrap(create):
    if inspect.iscoroutinefunction(create):
        @functools.wraps(create)
        async def acreate(*args, **kwargs):
            usage = current_usage()  # the request that made the call
            result = await create(*args, **kwargs)
            return _observe(result, bool(kwargs.get("stream")), usage)

        return acreate

    @functools.wraps(create)
    def screate(*args, **kwargs):
        usage = current_usage()
        result = create(*args, **kwargs)
        if inspect.isawaitable(result):  # an async client whose create isn't a coroutine function
            async def later():
                return _observe(await result, bool(kwargs.get("stream")), usage)

            return later()
        return _observe(result, bool(kwargs.get("stream")), usage)

    return screate


def _instrument(client, paths):
    if client is None or getattr(client, _PATCHED, False):
        return client
    patched = 0
    for path in paths:
        try:
            resource = client
            for name in path:
                resource = getattr(resource, name)
            create = resource.create
        except AttributeError:
            continue  # an older SDK without this API
        resource.create = _wrap(create)
        patched += 1
    if patched == 0:
        logger.warning("reqly: %s has none of the create methods to instrument", type(client).__name__)
        return client
    try:
        setattr(client, _PATCHED, True)
    except AttributeError:
        pass
    return client


def instrument_openai(client):
    """Count the tokens of every call made with this OpenAI (or OpenAI-
    compatible) client. Returns the client."""
    return _instrument(client, _OPENAI_METHODS)


def instrument_anthropic(client):
    """Count the tokens of every call made with this Anthropic client.
    Returns the client."""
    return _instrument(client, _ANTHROPIC_METHODS)
