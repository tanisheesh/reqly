"""instrument_openai / instrument_anthropic: tokens reach the request without
record_llm_usage() calls -- sync, async, streams. Fake clients always; the
real openai / anthropic packages (over a mock transport) when installed."""

import asyncio
import importlib.util
import json
from types import SimpleNamespace

import httpx
import pytest

import reqly
from reqly.core import request_context as rc


def _in_request(fn):
    token = rc.begin_request()
    fn()
    return rc.end_request(token)


def _usage(prompt, completion):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)


class _Create:
    def __init__(self, result):
        self.result = result

    def create(self, **kwargs):
        return self.result(kwargs) if callable(self.result) else self.result


class _AsyncCreate:
    def __init__(self, result):
        self.result = result

    async def create(self, **kwargs):
        return self.result


def test_sync_openai_calls_add_up_per_model():
    chat = SimpleNamespace(model="gpt-4o-mini", usage=_usage(100, 20))
    embed = SimpleNamespace(model="text-embedding-3-small", usage=SimpleNamespace(prompt_tokens=50, total_tokens=50))
    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create(chat)), embeddings=_Create(embed))
    assert reqly.instrument_openai(client) is client
    reqly.instrument_openai(client)  # twice: still counted once

    def calls():
        client.chat.completions.create(model="gpt-4o-mini", messages=[])
        client.chat.completions.create(model="gpt-4o-mini", messages=[])
        client.embeddings.create(model="text-embedding-3-small", input="x")

    assert _in_request(calls) == (
        "gpt-4o-mini", 250, 40, (("gpt-4o-mini", 200, 40), ("text-embedding-3-small", 50, 0)),
    )


def test_outside_a_request_nothing_is_recorded_and_nothing_breaks():
    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create(SimpleNamespace(model="m", usage=None))))
    reqly.instrument_openai(client)
    assert client.chat.completions.create(model="m").model == "m"


def test_async_anthropic_and_a_chat_stream():
    message = SimpleNamespace(model="claude-haiku-4-5", usage=SimpleNamespace(input_tokens=900, output_tokens=100))
    client = SimpleNamespace(messages=_AsyncCreate(message))
    reqly.instrument_anthropic(client)

    chunks = [SimpleNamespace(model="gpt-4o", usage=None, choices=[1]),
              SimpleNamespace(model="gpt-4o", usage=_usage(300, 30), choices=[])]
    streaming = SimpleNamespace(chat=SimpleNamespace(completions=_Create(lambda kw: iter(chunks))))
    reqly.instrument_openai(streaming)

    async def calls():
        await client.messages.create(model="claude-haiku-4-5", messages=[])
        stream = streaming.chat.completions.create(model="gpt-4o", stream=True)
        assert list(stream) == chunks  # passed through unchanged

    token = rc.begin_request()
    asyncio.run(calls())
    summary = rc.end_request(token)
    assert summary[3] == (("claude-haiku-4-5", 900, 100), ("gpt-4o", 300, 30))


def test_anthropic_stream_events():
    events = [
        {"type": "message_start", "message": {"model": "claude-sonnet-4-5", "usage": {"input_tokens": 1200, "output_tokens": 1}}},
        {"type": "content_block_delta", "delta": {"text": "hi"}},
        {"type": "message_delta", "usage": {"output_tokens": 340}},
        {"type": "message_stop"},
    ]
    client = SimpleNamespace(messages=_Create(lambda kw: iter(events)))
    reqly.instrument_anthropic(client)
    summary = _in_request(lambda: [*client.messages.create(model="x", stream=True)])
    assert summary[:3] == ("claude-sonnet-4-5", 1200, 340)


def test_a_stream_read_later_still_counts_for_its_request():
    chunks = [SimpleNamespace(model="gpt-4o", usage=_usage(10, 5))]
    client = SimpleNamespace(chat=SimpleNamespace(completions=_Create(lambda kw: iter(chunks))))
    reqly.instrument_openai(client)
    token = rc.begin_request()
    usage = rc.current_usage()
    stream = client.chat.completions.create(model="gpt-4o", stream=True)
    rc.end_request(token)
    list(stream)  # consumed after the handler returned (a streaming response)
    assert usage.summary()[:3] == ("gpt-4o", 10, 5)


# --- the real client libraries ---------------------------------------------------
# Newer openai / anthropic releases run on httpx2, older ones on httpx; same API.
_http = __import__("httpx2") if importlib.util.find_spec("httpx2") else httpx


def _http_for(client_module):
    source = open(client_module._base_client.__file__, encoding="utf-8").read()
    return _http if "import httpx2" in source else httpx


def _replying(http, payload):
    return http.MockTransport(lambda request: http.Response(200, json=payload(json.loads(request.content))))


def _chat_completion(body):
    return {
        "id": "c1", "object": "chat.completion", "created": 0, "model": body["model"],
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }


def test_real_openai_client():
    openai = pytest.importorskip("openai")
    http = _http_for(openai)
    client = reqly.instrument_openai(openai.OpenAI(
        api_key="k", base_url="http://llm.test/v1", http_client=http.Client(transport=_replying(http, _chat_completion)),
    ))
    summary = _in_request(lambda: client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]))
    assert summary[:3] == ("gpt-4o-mini", 12, 3)

    async def handler(request):
        return http.Response(200, json=_chat_completion(json.loads(request.content)))

    aclient = reqly.instrument_openai(openai.AsyncOpenAI(
        api_key="k", base_url="http://llm.test/v1", http_client=http.AsyncClient(transport=http.MockTransport(handler)),
    ))

    async def call():
        token = rc.begin_request()
        await aclient.chat.completions.create(model="gpt-4.1", messages=[{"role": "user", "content": "hi"}])
        return rc.end_request(token)

    assert asyncio.run(call())[:3] == ("gpt-4.1", 12, 3)


def test_real_anthropic_client():
    anthropic = pytest.importorskip("anthropic")
    http = _http_for(anthropic)
    message = {
        "id": "m1", "type": "message", "role": "assistant", "model": "claude-haiku-4-5",
        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 40, "output_tokens": 9},
    }
    client = reqly.instrument_anthropic(anthropic.Anthropic(
        api_key="k", base_url="http://llm.test", http_client=http.Client(transport=_replying(http, lambda body: message)),
    ))
    summary = _in_request(lambda: client.messages.create(
        model="claude-haiku-4-5", max_tokens=10, messages=[{"role": "user", "content": "hi"}]))
    assert summary[:3] == ("claude-haiku-4-5", 40, 9)
