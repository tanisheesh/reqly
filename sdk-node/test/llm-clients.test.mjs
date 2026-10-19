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
