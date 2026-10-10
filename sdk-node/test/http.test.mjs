import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";

import { recordLlmUsage, reqlyHttp } from "../dist/esm/index.js";
import { fakeCollector, options } from "./helpers.mjs";

let collector;
before(async () => {
  collector = await fakeCollector();
});
after(async () => {
  await collector.close();
});

function summary(events) {
  return events.map((e) => [e.method, e.route, e.status_code, e.error, e.error_type]);
}

async function serve(handler) {
  const server = createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { base: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((r) => server.close(r)) };
}

// A tiny router: what a framework without an integration would look like.
const ROUTES = [
  ["GET", /^\/users\/[^/]+$/, "/users/:id"],
  ["POST", /^\/chat$/, "/chat"],
  ["GET", /^\/boom$/, "/boom"],
  ["GET", /^\/async-boom$/, "/async-boom"],
];

function app(req, res) {
  const path = req.url.split("?")[0];
  const hit = ROUTES.find(([m, re]) => m === req.method && re.test(path));
  req.matchedRoute = hit?.[2];
  if (!hit) {
    res.statusCode = 404;
    return res.end("not found");
  }
  if (hit[2] === "/boom") throw new TypeError("bad input");
  if (hit[2] === "/async-boom") {
    return (async () => {
      await new Promise((r) => setTimeout(r, 2));
      throw new RangeError("later");
    })();
  }
  if (hit[2] === "/chat") {
    return (async () => {
      await new Promise((r) => setTimeout(r, 5));
      recordLlmUsage("gpt-4o-mini", 100, 20); // after an await
      res.statusCode = 201;
      res.end("ok");
    })();
  }
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify({ ok: true }));
}

test("http: routes from the resolver, 404s, sync and async errors, LLM usage, query strings", async () => {
  collector.batches.length = 0;
  const handler = reqlyHttp(
    (req, res) => {
      try {
        const out = app(req, res);
        // a real server would send a 500; do it here so the client gets a response
        return out?.catch?.((err) => {
          res.statusCode = 500;
          res.end();
          throw err;
        });
      } catch (err) {
        res.statusCode = 500;
        res.end();
        throw err;
      }
    },
    { ...options(collector.url, { consumerHeader: "x-tenant", hashConsumer: false }), routeResolver: (req) => req.matchedRoute },
  );
  const errors = [];
  const { base, close } = await serve((req, res) => {
    try {
      const out = handler(req, res);
      out?.catch?.((err) => errors.push(err.name));
    } catch (err) {
      errors.push(err.name);
    }
  });
  await fetch(`${base}/users/7?expand=1`, { headers: { "x-tenant": "acme" } });
  await fetch(`${base}/chat`, { method: "POST" });
  await fetch(`${base}/boom`);
  await fetch(`${base}/async-boom`);
  await fetch(`${base}/nope`);
  await close();
  await handler.client.flush();

  const events = collector.events();
  assert.deepEqual(summary(events), [
    ["GET", "/users/:id", 200, false, null],
    ["POST", "/chat", 201, false, null],
    ["GET", "/boom", 500, true, "TypeError"],
    ["GET", "/async-boom", 500, true, "RangeError"],
    ["GET", "__unmatched__", 404, false, null],
  ]);
  assert.deepEqual(errors, ["TypeError", "RangeError"]); // rethrown to the caller
  assert.equal(events[0].consumer_id, "acme");
  assert.deepEqual([events[1].llm_model, events[1].llm_input_tokens], ["gpt-4o-mini", 100]);
  await handler.client.shutdown();
});

test("http: no resolver records __unmatched__, a throwing resolver too, never the raw path", async () => {
  collector.batches.length = 0;
  const plain = reqlyHttp((req, res) => res.end("ok"), options(collector.url));
  const broken = reqlyHttp((req, res) => res.end("ok"), {
    client: plain.client,
    routeResolver: () => {
      throw new Error("resolver bug");
    },
  });
  const a = await serve(plain);
  const b = await serve(broken);
  await fetch(`${a.base}/users/42`);
  await fetch(`${b.base}/users/43`);
  await a.close();
  await b.close();
  await plain.client.flush();
  assert.deepEqual(summary(collector.events()), [
    ["GET", "__unmatched__", 200, false, null],
    ["GET", "__unmatched__", 200, false, null],
  ]);
  await plain.client.shutdown();
});
