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
    with open(client_module._base_client.__file__, encoding="utf-8") as f:
        source = f.read()
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


def _sse(http, events):
    body = "".join(
        (f"event: {name}\n" if name else "") + f"data: {json.dumps(data) if not isinstance(data, str) else data}\n\n"
        for name, data in events
    )
    return http.MockTransport(lambda request: http.Response(
        200, headers={"content-type": "text/event-stream"}, content=body.encode()))


_ANTHROPIC_STREAM = [
    ("message_start", {"type": "message_start", "message": {
        "id": "m1", "type": "message", "role": "assistant", "model": "claude-opus-4-7", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1200, "output_tokens": 1}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                       "usage": {"output_tokens": 340}}),
    ("message_stop", {"type": "message_stop"}),
]


@pytest.mark.parametrize("consume", ["iterate", "text_stream", "final_message"])
def test_real_anthropic_stream_helper(consume):
    anthropic = pytest.importorskip("anthropic")
    http = _http_for(anthropic)
    client = reqly.instrument_anthropic(anthropic.Anthropic(
        api_key="k", base_url="http://llm.test", http_client=http.Client(transport=_sse(http, _ANTHROPIC_STREAM)),
    ))

    def call():
        with client.messages.stream(model="claude-opus-4-7", max_tokens=10,
                                    messages=[{"role": "user", "content": "hi"}]) as stream:
            if consume == "iterate":
                assert any(e.type == "message_stop" for e in stream)
            elif consume == "text_stream":
                assert "".join(stream.text_stream) == "hi"
            else:
                assert stream.get_final_message().usage.output_tokens == 340

    assert _in_request(call)[:3] == ("claude-opus-4-7", 1200, 340)


def test_real_openai_stream_helpers():
    openai = pytest.importorskip("openai")
    http = _http_for(openai)
    chunk = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "gpt-4o"}
    chat_events = [
        (None, {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hi"}, "finish_reason": None}]}),
        (None, {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        (None, {**chunk, "choices": [], "usage": {"prompt_tokens": 300, "completion_tokens": 30, "total_tokens": 330}}),
        (None, "[DONE]"),
    ]
    chat = reqly.instrument_openai(openai.OpenAI(
        api_key="k", base_url="http://llm.test/v1", http_client=http.Client(transport=_sse(http, chat_events)),
    ))

    def chat_call():
        with chat.chat.completions.stream(model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
                                          stream_options={"include_usage": True}) as stream:
            stream.until_done()

    assert _in_request(chat_call)[:3] == ("gpt-4o", 300, 30)

    response = {"id": "r1", "object": "response", "created_at": 0, "model": "gpt-4.1", "status": "completed",
                "output": [], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
                "usage": {"input_tokens": 50, "output_tokens": 7, "total_tokens": 57,
                          "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
    responses_events = [
        ("response.created", {"type": "response.created", "sequence_number": 0, "response": {**response, "status": "in_progress", "usage": None}}),
        ("response.completed", {"type": "response.completed", "sequence_number": 1, "response": response}),
    ]
    responses = reqly.instrument_openai(openai.OpenAI(
        api_key="k", base_url="http://llm.test/v1", http_client=http.Client(transport=_sse(http, responses_events)),
    ))

    def responses_call():
        with responses.responses.stream(model="gpt-4.1", input="hi") as stream:
            assert stream.get_final_response().usage.output_tokens == 7

    assert _in_request(responses_call)[:3] == ("gpt-4.1", 50, 7)


def test_real_async_stream_helpers():
    openai = pytest.importorskip("openai")
    anthropic = pytest.importorskip("anthropic")
    oh, ah = _http_for(openai), _http_for(anthropic)
    chunk = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "gpt-4o"}
    chat_events = [
        (None, {**chunk, "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]}),
        (None, {**chunk, "choices": [], "usage": {"prompt_tokens": 300, "completion_tokens": 30, "total_tokens": 330}}),
        (None, "[DONE]"),
    ]

    def async_sse(http, events):
        transport = _sse(http, events)

        async def handler(request):
            return transport.handler(request)

        return http.MockTransport(handler)

    chat = reqly.instrument_openai(openai.AsyncOpenAI(
        api_key="k", base_url="http://llm.test/v1", http_client=oh.AsyncClient(transport=async_sse(oh, chat_events))))
    claude = reqly.instrument_anthropic(anthropic.AsyncAnthropic(
        api_key="k", base_url="http://llm.test", http_client=ah.AsyncClient(transport=async_sse(ah, _ANTHROPIC_STREAM))))

    async def calls():
        token = rc.begin_request()
        async with chat.chat.completions.stream(model="gpt-4o", messages=[{"role": "user", "content": "hi"}],
                                                stream_options={"include_usage": True}) as stream:
            async for _ in stream:
                pass
        async with claude.messages.stream(model="claude-opus-4-7", max_tokens=10,
                                          messages=[{"role": "user", "content": "hi"}]) as stream:
            await stream.get_final_message()
        return rc.end_request(token)

    assert asyncio.run(calls())[3] == (("claude-opus-4-7", 1200, 340), ("gpt-4o", 300, 30))
