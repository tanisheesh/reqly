import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import express from "express";
import Fastify from "fastify";
import { Hono } from "hono";

import { recordLlmResponse, recordLlmUsage, reqlyExpress, reqlyFastify, reqlyHono } from "../dist/esm/index.js";
import { fakeCollector, hmac16, options } from "./helpers.mjs";

let collector;
before(async () => {
  collector = await fakeCollector();
});
after(async () => {
  await collector.close();
});

async function listen(app) {
  const server = await new Promise((resolve) => {
    const s = app.listen(0, "127.0.0.1", () => resolve(s));
  });
  return { base: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((r) => server.close(r)) };
}

function summary(events) {
  return events.map((e) => [e.method, e.route, e.status_code, e.error]);
}

test("express: route templates with mount paths, 404s, errors, consumers and LLM usage", async () => {
  collector.batches.length = 0;
  const reqly = reqlyExpress(options(collector.url, { consumerHeader: "X-API-Key", consumerSalt: "s3cret" }));
  const app = express();
  app.use(reqly);
  const api = express.Router();
  api.get("/users/:id", (req, res) => res.json({ id: req.params.id }));
  api.post("/chat", async (req, res) => {
    await new Promise((r) => setTimeout(r, 5)); // usage recorded after an await
    recordLlmUsage("gpt-4o-mini", 100, 20);
    recordLlmResponse({ model: "gpt-4o-mini", usage: { prompt_tokens: 50, completion_tokens: 5 } });
    res.status(201).send("ok");
  });
  api.get("/boom", () => {
    throw new TypeError("bad input");
  });
  api.get("/missing", (req, res, next) => next(Object.assign(new Error("no such thing"), { status: 404 })));
  app.use("/api", api);
  app.use(reqly.errorHandler);
  app.use((err, req, res, next) => res.status(err.status ?? 500).send("error")); // eslint-disable-line no-unused-vars

  const { base, close } = await listen(app);
  await fetch(`${base}/api/users/7`, { headers: { "X-API-Key": "key_live_1" } });
  await fetch(`${base}/api/chat`, { method: "POST" });
  await fetch(`${base}/api/boom`);
  await fetch(`${base}/api/missing`);
  await fetch(`${base}/nope`);
  await close();
  await reqly.client.flush();

  const events = collector.events();
  assert.deepEqual(summary(events), [
    ["GET", "/api/users/:id", 200, false],
    ["POST", "/api/chat", 201, false],
    ["GET", "/api/boom", 500, true],
    ["GET", "/api/missing", 404, false], // a 4xx error object is the app answering, not an error
    ["GET", "__unmatched__", 404, false],
  ]);
  assert.equal(events[3].error_type, null);
  assert.equal(events[0].consumer_id, hmac16("key_live_1", "s3cret"));
  assert.equal(events[1].consumer_id, null);
  assert.deepEqual([events[1].llm_model, events[1].llm_input_tokens, events[1].llm_output_tokens], ["gpt-4o-mini", 150, 25]);
  assert.equal(events[0].llm_model, null);
  assert.equal(events[2].error_type, "TypeError");
  assert.equal(collector.batches.at(-1).service_name, "node-test");
  assert.equal(collector.requests.at(-1).headers["x-reqly-key"], "k");
  await reqly.client.shutdown();
});

test("fastify: route templates, 404s, errors, consumer function and LLM usage", async () => {
  collector.batches.length = 0;
  const plugin = reqlyFastify(
    options(collector.url, { consumer: (info) => info.headers["x-tenant"], hashConsumer: false }),
  );
  const app = Fastify();
  await app.register(plugin);
  app.get("/items/:id", async (req) => ({ id: req.params.id }));
  app.post("/summarize", async () => {
    await new Promise((r) => setTimeout(r, 5));
    recordLlmResponse({ model: "claude-haiku-4-5", usage: { input_tokens: 900, output_tokens: 100 } });
    return "ok";
  });
  app.get("/boom", async () => {
    throw new RangeError("nope");
  });
  await app.inject({ method: "GET", url: "/items/3", headers: { "x-tenant": "acme" } });
  await app.inject({ method: "POST", url: "/summarize" });
  await app.inject({ method: "GET", url: "/boom" });
  await app.inject({ method: "GET", url: "/missing" });
  await app.close();
  await plugin.client.flush();

  const events = collector.events();
  assert.deepEqual(summary(events), [
    ["GET", "/items/:id", 200, false],
    ["POST", "/summarize", 200, false],
    ["GET", "/boom", 500, true],
    ["GET", "__unmatched__", 404, false],
  ]);
  assert.equal(events[0].consumer_id, "acme");
  assert.deepEqual([events[1].llm_model, events[1].llm_input_tokens], ["claude-haiku-4-5", 900]);
  assert.equal(events[2].error_type, "RangeError");
  await plugin.client.shutdown();
});

test("hono: matched handler path, 404s, errors and LLM usage", async () => {
  collector.batches.length = 0;
  const middleware = reqlyHono(options(collector.url));
  const app = new Hono();
  app.use(middleware);
  app.get("/posts/:slug", (c) => c.json({ slug: c.req.param("slug") }));
  app.post("/ask", async (c) => {
    await new Promise((r) => setTimeout(r, 5));
    recordLlmUsage("gpt-4o", 10, 2);
    return c.text("ok", 201);
  });
  app.get("/boom", () => {
    throw new SyntaxError("broken");
  });
  await app.request("/posts/hello");
  await app.request("/ask", { method: "POST" });
  await app.request("/boom");
  await app.request("/missing");
  await middleware.client.flush();

  const events = collector.events();
  assert.deepEqual(summary(events), [
    ["GET", "/posts/:slug", 200, false],
    ["POST", "/ask", 201, false],
    ["GET", "/boom", 500, true],
    ["GET", "__unmatched__", 404, false],
  ]);
  assert.equal(events[1].llm_model, "gpt-4o");
  assert.equal(events[2].error_type, "SyntaxError");
  await middleware.client.shutdown();
});

test("express 4: same templates, including errors inside a mounted router", async () => {
  const { default: express4 } = await import("express4");
  collector.batches.length = 0;
  const reqly = reqlyExpress(options(collector.url));
  const app = express4();
  app.use(reqly);
  const api = express4.Router();
  api.get("/orders/:id", (req, res) => res.send("ok"));
  api.get("/boom", () => {
    throw new Error("x");
  });
  app.use("/v1", api);
  app.use(reqly.errorHandler);
  app.use((err, req, res, next) => res.status(500).send("error")); // eslint-disable-line no-unused-vars
  const { base, close } = await listen(app);
  await fetch(`${base}/v1/orders/1`);
  await fetch(`${base}/v1/boom`);
  await close();
  await reqly.client.flush();
  assert.deepEqual(summary(collector.events()), [
    ["GET", "/v1/orders/:id", 200, false],
    ["GET", "/v1/boom", 500, true],
  ]);
  await reqly.client.shutdown();
});

test("fastify 4: same templates", async () => {
  const { default: Fastify4 } = await import("fastify4");
  collector.batches.length = 0;
  const plugin = reqlyFastify(options(collector.url));
  const app = Fastify4();
  await app.register(plugin);
  app.get("/items/:id", async () => "ok");
  await app.inject({ method: "GET", url: "/items/1" });
  await app.inject({ method: "GET", url: "/missing" });
  await app.close();
  await plugin.client.flush();
  assert.deepEqual(summary(collector.events()), [
    ["GET", "/items/:id", 200, false],
    ["GET", "__unmatched__", 404, false],
  ]);
  await plugin.client.shutdown();
});

test("registered twice, a request is still recorded once", async () => {
  collector.batches.length = 0;
  const opts = options(collector.url);

  const e1 = reqlyExpress(opts);
  const e2 = reqlyExpress(opts);
  const eapp = express();
  eapp.use(e1);
  eapp.use(e2);
  eapp.get("/e", (req, res) => res.send("ok"));
  const { base, close } = await listen(eapp);
  await fetch(`${base}/e`);
  await close();

  const f1 = reqlyFastify(opts);
  const f2 = reqlyFastify(opts);
  const fapp = Fastify();
  await fapp.register(f1);
  await fapp.register(f2);
  fapp.get("/f", async () => "ok");
  await fapp.inject({ method: "GET", url: "/f" });
  await fapp.close();

  const h1 = reqlyHono(opts);
  const h2 = reqlyHono(opts);
  const happ = new Hono();
  happ.use(h1);
  happ.use(h2);
  happ.get("/h", (c) => c.text("ok"));
  await happ.request("/h");

  for (const c of [e1.client, e2.client, f1.client, f2.client, h1.client, h2.client]) await c.flush();
  assert.deepEqual(collector.events().map((e) => e.route).sort(), ["/e", "/f", "/h"]);
  for (const c of [e1.client, e2.client, f1.client, f2.client, h1.client, h2.client]) await c.shutdown();
});

test("hono: app.all routes are recorded; 404s behind a wildcard middleware are unmatched", async () => {
  collector.batches.length = 0;
  const middleware = reqlyHono(options(collector.url));
  const app = new Hono();
  app.use(middleware);
  app.use("/api/*", async (c, next) => next());
  app.all("/any", (c) => c.text("all"));
  app.get("/api/items/:id", (c) => c.text("item"));
  app.get("/static/*", (c) => c.text("file"));
  await app.request("/any", { method: "POST" });
  await app.request("/api/items/1");
  await app.request("/api/missing");
  await app.request("/static/a.css");
  await middleware.client.flush();
  assert.deepEqual(summary(collector.events()), [
    ["POST", "/any", 200, false],
    ["GET", "/api/items/:id", 200, false],
    ["GET", "__unmatched__", 404, false],
    ["GET", "/static/*", 200, false],
  ]);
  await middleware.client.shutdown();
});
