import assert from "node:assert/strict";
import { createServer } from "node:http";
import { Readable } from "node:stream";
import { after, before, test } from "node:test";

import Router from "@koa/router";
import express from "express";
import Fastify from "fastify";
import Koa from "koa";

import { reqlyExpress, reqlyFastify, reqlyHttp, reqlyKoa } from "../dist/esm/index.js";
import { fakeCollector, options } from "./helpers.mjs";

let collector;
before(async () => {
  collector = await fakeCollector();
});
after(async () => {
  await collector.close();
});

// 3 chunks, no Content-Length: like an LLM token stream or server-sent events
const CHUNKS = ["data: héllo\n\n", "data: wörld\n\n", "data: [DONE]\n\n"];
const STREAM_BYTES = CHUNKS.reduce((n, c) => n + Buffer.byteLength(c), 0);

async function get(base, path) {
  const res = await fetch(base + path);
  return res.text();
}

async function listen(handlerOrApp) {
  const server = typeof handlerOrApp === "function" ? createServer(handlerOrApp) : handlerOrApp;
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { base: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((r) => server.close(r)) };
}

function bytesByRoute() {
  return Object.fromEntries(collector.events().map((e) => [e.route, e.response_bytes]));
}

test("express: streamed bodies are counted, Content-Length is used when set", async () => {
  collector.batches.length = 0;
  const reqly = reqlyExpress(options(collector.url));
  const app = express();
  app.use(reqly);
  app.get("/stream", (req, res) => {
    for (const c of CHUNKS) res.write(c);
    res.end();
  });
  app.get("/json", (req, res) => res.json({ ok: true }));
  const { base, close } = await listen(createServer(app));
  assert.equal(await get(base, "/stream"), CHUNKS.join(""));
  await get(base, "/json");
  await close();
  await reqly.client.flush();
  assert.deepEqual(bytesByRoute(), { "/stream": STREAM_BYTES, "/json": 11 });
  await reqly.client.shutdown();
});

test("fastify: a streamed reply is counted; a 400 is not an error", async () => {
  collector.batches.length = 0;
  const plugin = reqlyFastify(options(collector.url));
  const app = Fastify();
  await app.register(plugin);
  app.get("/stream", (req, reply) => {
    reply.type("text/event-stream").send(Readable.from(CHUNKS));
  });
  app.post("/validated", { schema: { body: { type: "object", required: ["name"] } } }, async () => "ok");
  await app.listen({ port: 0, host: "127.0.0.1" });
  const base = `http://127.0.0.1:${app.server.address().port}`;
  await get(base, "/stream");
  await fetch(`${base}/validated`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
  await app.close();
  await plugin.client.flush();
  const events = collector.events();
  assert.equal(bytesByRoute()["/stream"], STREAM_BYTES);
  const validated = events.find((e) => e.route === "/validated");
  assert.deepEqual([validated.status_code, validated.error, validated.error_type], [400, false, null]);
  await plugin.client.shutdown();
});

test("koa: a stream body is counted", async () => {
  collector.batches.length = 0;
  const reqly = reqlyKoa(options(collector.url));
  const app = new Koa();
  app.use(reqly);
  const router = new Router();
  router.get("/stream", (ctx) => {
    ctx.type = "text/event-stream";
    ctx.body = Readable.from(CHUNKS);
  });
  app.use(router.routes());
  const { base, close } = await listen(createServer(app.callback()));
  await get(base, "/stream");
  await close();
  await reqly.client.flush();
  assert.equal(bytesByRoute()["/stream"], STREAM_BYTES);
  await reqly.client.shutdown();
});

test("reqlyHttp: chunks written to the response are counted", async () => {
  collector.batches.length = 0;
  const handler = reqlyHttp(
    (req, res) => {
      for (const c of CHUNKS) res.write(Buffer.from(c));
      res.end();
    },
    { ...options(collector.url), routeResolver: () => "/stream" },
  );
  const { base, close } = await listen(handler);
  await get(base, "/stream");
  await close();
  await handler.client.flush();
  assert.equal(bytesByRoute()["/stream"], STREAM_BYTES);
  await handler.client.shutdown();
});
