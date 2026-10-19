import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";

import { instrumentAnthropic, instrumentOpenAI, reqlyHttp } from "../dist/esm/index.js";
import { fakeCollector, options } from "./helpers.mjs";

let collector;
before(async () => {
  collector = await fakeCollector();
});
after(async () => {
  await collector.close();
});

/** Serves `handler` instrumented, makes one request, returns its event. */
async function oneRequest(handler) {
  collector.batches.length = 0;
  const instrumented = reqlyHttp(
    async (req, res) => {
      await handler();
      res.end("ok");
    },
    { ...options(collector.url), routeResolver: () => "/llm" },
  );
  const server = createServer(instrumented);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  await fetch(`http://127.0.0.1:${server.address().port}/llm`);
  await new Promise((resolve) => server.close(resolve));
  await instrumented.client.shutdown();
  return collector.events()[0];
}

const resource = (result) => ({ create: async (params) => (typeof result === "function" ? result(params) : result) });

async function* chunks(items) {
  for (const item of items) yield item;
}

test("openai and anthropic calls are counted per model, without recordLlmUsage", async () => {
  const openai = instrumentOpenAI({
    chat: { completions: resource((p) => ({ model: p.model, usage: { prompt_tokens: 100, completion_tokens: 20 } })) },
    embeddings: resource({ model: "text-embedding-3-small", usage: { prompt_tokens: 50, total_tokens: 50 } }),
  });
  instrumentOpenAI(openai); // twice: counted once
  const anthropic = instrumentAnthropic({
    messages: resource({ model: "claude-haiku-4-5", usage: { input_tokens: 900, output_tokens: 100 } }),
  });
  const event = await oneRequest(async () => {
    await openai.chat.completions.create({ model: "gpt-4o-mini", messages: [] });
    await openai.embeddings.create({ model: "text-embedding-3-small", input: "x" });
    await anthropic.messages.create({ model: "claude-haiku-4-5", messages: [] });
  });
  assert.deepEqual([event.llm_model, event.llm_input_tokens, event.llm_output_tokens], ["claude-haiku-4-5", 1050, 120]);
  assert.deepEqual(event.llm_usage.map((m) => m.model), ["claude-haiku-4-5", "gpt-4o-mini", "text-embedding-3-small"]);
});

test("streams count when they finish, and pass their chunks through", async () => {
  const openai = instrumentOpenAI({
    chat: {
      completions: resource(() =>
        chunks([
          { model: "gpt-4o", choices: [{ delta: { content: "hi" } }], usage: null },
          { model: "gpt-4o", choices: [], usage: { prompt_tokens: 300, completion_tokens: 30 } },
        ]),
      ),
    },
  });
  const anthropic = instrumentAnthropic({
    messages: resource(() =>
      chunks([
        { type: "message_start", message: { model: "claude-sonnet-4-5", usage: { input_tokens: 1200, output_tokens: 1 } } },
        { type: "content_block_delta", delta: { text: "hi" } },
        { type: "message_delta", usage: { output_tokens: 340 } },
        { type: "message_stop" },
      ]),
    ),
  });
  let seen = 0;
  const event = await oneRequest(async () => {
    for await (const _ of await openai.chat.completions.create({ model: "gpt-4o", stream: true })) seen += 1;
    for await (const _ of await anthropic.messages.create({ model: "x", stream: true })) seen += 1;
  });
  assert.equal(seen, 6);
  assert.deepEqual(
    event.llm_usage.map((m) => [m.model, m.input_tokens, m.output_tokens]),
    [["claude-sonnet-4-5", 1200, 340], ["gpt-4o", 300, 30]],
  );
});

test("a failing call still fails for the app, and nothing is counted", async () => {
  const openai = instrumentOpenAI({ chat: { completions: { create: async () => { throw new Error("rate limited"); } } } });
  const event = await oneRequest(async () => {
    await assert.rejects(openai.chat.completions.create({ model: "m" }), /rate limited/);
  });
  assert.equal(event.llm_model, null);
});

test("the real openai client (Node 22+)", async (t) => {
  let OpenAI;
  try {
    ({ default: OpenAI } = await import("openai"));
  } catch {
    return t.skip("openai isn't importable on this Node");
  }
  const reply = async (url, init) => {
    const body = JSON.parse(init.body);
    return new Response(JSON.stringify({
      id: "c1", object: "chat.completion", created: 0, model: body.model,
      choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: "ok" } }],
      usage: { prompt_tokens: 12, completion_tokens: 3, total_tokens: 15 },
    }), { status: 200, headers: { "content-type": "application/json" } });
  };
  const client = instrumentOpenAI(new OpenAI({ apiKey: "k", baseURL: "http://llm.test/v1", fetch: reply }));
  const event = await oneRequest(async () => {
    const { data } = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [] }).withResponse();
    assert.equal(data.choices[0].message.content, "ok"); // the provider's promise, untouched
  });
  assert.deepEqual([event.llm_model, event.llm_input_tokens, event.llm_output_tokens], ["gpt-4o-mini", 12, 3]);
});

test("the real anthropic client", async (t) => {
  let Anthropic;
  try {
    ({ default: Anthropic } = await import("@anthropic-ai/sdk"));
  } catch {
    return t.skip("@anthropic-ai/sdk isn't importable on this Node");
  }
  const reply = async () =>
    new Response(JSON.stringify({
      id: "m1", type: "message", role: "assistant", model: "claude-haiku-4-5",
      content: [{ type: "text", text: "ok" }], stop_reason: "end_turn", stop_sequence: null,
      usage: { input_tokens: 40, output_tokens: 9 },
    }), { status: 200, headers: { "content-type": "application/json" } });
  const client = instrumentAnthropic(new Anthropic({ apiKey: "k", baseURL: "http://llm.test", fetch: reply }));
  const event = await oneRequest(async () => {
    await client.messages.create({ model: "claude-haiku-4-5", max_tokens: 10, messages: [{ role: "user", content: "hi" }] });
  });
  assert.deepEqual([event.llm_model, event.llm_input_tokens, event.llm_output_tokens], ["claude-haiku-4-5", 40, 9]);
});

const sse = (events) => async () =>
  new Response(
    events.map(([name, data]) => `${name ? `event: ${name}\n` : ""}data: ${typeof data === "string" ? data : JSON.stringify(data)}\n\n`).join(""),
    { status: 200, headers: { "content-type": "text/event-stream" } },
  );

test("the real clients' stream() helpers are counted once (Node 22+)", async (t) => {
  let OpenAI, Anthropic;
  try {
    ({ default: OpenAI } = await import("openai"));
    ({ default: Anthropic } = await import("@anthropic-ai/sdk"));
  } catch {
    return t.skip("openai / @anthropic-ai/sdk aren't importable on this Node");
  }
  const chunk = { id: "c1", object: "chat.completion.chunk", created: 0, model: "gpt-4o" };
  const chat = instrumentOpenAI(new OpenAI({ apiKey: "k", baseURL: "http://llm.test/v1", fetch: sse([
    [null, { ...chunk, choices: [{ index: 0, delta: { role: "assistant", content: "hi" }, finish_reason: "stop" }] }],
    [null, { ...chunk, choices: [], usage: { prompt_tokens: 300, completion_tokens: 30, total_tokens: 330 } }],
    [null, "[DONE]"],
  ]) }));
  const response = { id: "r1", object: "response", created_at: 0, model: "gpt-4.1", status: "completed", output: [],
    parallel_tool_calls: true, tool_choice: "auto", tools: [],
    usage: { input_tokens: 50, output_tokens: 7, total_tokens: 57, input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 0 } } };
  const responses = instrumentOpenAI(new OpenAI({ apiKey: "k", baseURL: "http://llm.test/v1", fetch: sse([
    ["response.created", { type: "response.created", sequence_number: 0, response: { ...response, status: "in_progress", usage: null } }],
    ["response.completed", { type: "response.completed", sequence_number: 1, response }],
  ]) }));
  const claude = instrumentAnthropic(new Anthropic({ apiKey: "k", baseURL: "http://llm.test", fetch: sse([
    ["message_start", { type: "message_start", message: { id: "m1", type: "message", role: "assistant", model: "claude-opus-4-7",
      content: [], stop_reason: null, stop_sequence: null, usage: { input_tokens: 1200, output_tokens: 1 } } }],
    ["content_block_start", { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }],
    ["content_block_delta", { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "hi" } }],
    ["content_block_stop", { type: "content_block_stop", index: 0 }],
    ["message_delta", { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 340 } }],
    ["message_stop", { type: "message_stop" }],
  ]) }));

  const event = await oneRequest(async () => {
    await chat.chat.completions
      .stream({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }], stream_options: { include_usage: true } })
      .finalChatCompletion();
    await responses.responses.stream({ model: "gpt-4.1", input: "hi" }).finalResponse();
    const message = await claude.messages
      .stream({ model: "claude-opus-4-7", max_tokens: 10, messages: [{ role: "user", content: "hi" }] })
      .finalMessage();
    assert.equal(message.usage.output_tokens, 340);
  });
  assert.deepEqual(
    event.llm_usage.map((m) => [m.model, m.input_tokens, m.output_tokens]),
    [["claude-opus-4-7", 1200, 340], ["gpt-4o", 300, 30], ["gpt-4.1", 50, 7]],
  );
});
