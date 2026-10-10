import "reflect-metadata";
import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import Router from "@koa/router";
import { Controller, Get, HttpException, Module, NotFoundException, Post, Req } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import { FastifyAdapter } from "@nestjs/platform-fastify";
import Koa from "koa";

import { recordLlmUsage, reqlyKoa, reqlyNest } from "../dist/esm/index.js";
import { fakeCollector, hmac16, options } from "./helpers.mjs";

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

// --- Koa -----------------------------------------------------------------------

test("koa: router templates with prefixes, thrown errors, ctx.throw, 404/405, consumers and LLM usage", async () => {
  collector.batches.length = 0;
  const reqly = reqlyKoa(options(collector.url, { consumerHeader: "X-API-Key", consumerSalt: "s3cret" }));
  const app = new Koa();
  app.silent = true; // no stderr for the thrown errors below
  app.use(reqly);

  const users = new Router();
  users.get("/:id", (ctx) => {
    ctx.body = { id: ctx.params.id };
  });
  const api = new Router({ prefix: "/api" });
  api.use("/users", users.routes());
  api.post("/chat", async (ctx) => {
    await new Promise((r) => setTimeout(r, 5)); // usage recorded after an await
    recordLlmUsage("gpt-4o-mini", 100, 20);
    ctx.status = 201;
    ctx.body = "ok";
  });
  api.get("/boom", () => {
    throw new TypeError("bad input");
  });
  api.get("/missing", (ctx) => ctx.throw(404, "no such thing"));
  app.use(api.routes()).use(api.allowedMethods());

  const server = await new Promise((resolve) => {
    const s = app.listen(0, "127.0.0.1", () => resolve(s));
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  await fetch(`${base}/api/users/7`, { headers: { "X-API-Key": "key_live_1" } });
  await fetch(`${base}/api/chat`, { method: "POST" });
  await fetch(`${base}/api/boom`);
  await fetch(`${base}/api/missing`);
  await fetch(`${base}/nope`);
  await fetch(`${base}/api/users/7`, { method: "DELETE" });
  await new Promise((r) => server.close(r));
  await reqly.client.flush();

  const events = collector.events();
  assert.deepEqual(summary(events), [
    ["GET", "/api/users/:id", 200, false, null],
    ["POST", "/api/chat", 201, false, null],
    ["GET", "/api/boom", 500, true, "TypeError"],
    ["GET", "/api/missing", 404, false, null], // ctx.throw(404) is not a server error
    ["GET", "__unmatched__", 404, false, null],
    ["DELETE", "__unmatched__", 405, false, null],
  ]);
  assert.equal(events[0].consumer_id, hmac16("key_live_1", "s3cret"));
  assert.deepEqual([events[1].llm_model, events[1].llm_input_tokens, events[1].llm_output_tokens], ["gpt-4o-mini", 100, 20]);
  assert.ok(events[0].response_bytes > 0);
  await reqly.client.shutdown();
});

test("koa: registered twice, a request is still recorded once", async () => {
  collector.batches.length = 0;
  const reqly = reqlyKoa(options(collector.url));
  const app = new Koa();
  app.use(reqly);
  app.use(reqly);
  const router = new Router();
  router.get("/ping", (ctx) => {
    ctx.body = "pong";
  });
  app.use(router.routes());
  const server = await new Promise((resolve) => {
    const s = app.listen(0, "127.0.0.1", () => resolve(s));
  });
  await fetch(`http://127.0.0.1:${server.address().port}/ping`);
  await new Promise((r) => server.close(r));
  await reqly.client.flush();
  assert.deepEqual(summary(collector.events()), [["GET", "/ping", 200, false, null]]);
  await reqly.client.shutdown();
});

// --- NestJS ----------------------------------------------------------------------

// Decorators applied by hand: plain JavaScript has no decorator syntax.
class UsersController {
  get(req) {
    return { id: req.params.id };
  }
  async chat() {
    await new Promise((r) => setTimeout(r, 5));
    recordLlmUsage("gpt-4o-mini", 100, 20);
    return "ok";
  }
  boom() {
    throw new TypeError("bad input");
  }
  missing() {
    throw new NotFoundException();
  }
  failing() {
    throw new HttpException("upstream down", 503);
  }
}
const method = (name) => Object.getOwnPropertyDescriptor(UsersController.prototype, name);
Controller("users")(UsersController);
Get(":id")(UsersController.prototype, "get", method("get"));
Req()(UsersController.prototype, "get", 0);
Post("x/chat")(UsersController.prototype, "chat", method("chat"));
Get("x/boom")(UsersController.prototype, "boom", method("boom"));
Get("x/missing")(UsersController.prototype, "missing", method("missing"));
Get("x/failing")(UsersController.prototype, "failing", method("failing"));
class AppModule {}
Module({ controllers: [UsersController] })(AppModule);

for (const adapter of ["express", "fastify"]) {
  test(`nest (${adapter}): global prefix, 404s, exception types and LLM usage`, async () => {
    collector.batches.length = 0;
    const app =
      adapter === "express"
        ? await NestFactory.create(AppModule, { logger: false })
        : await NestFactory.create(AppModule, new FastifyAdapter(), { logger: false });
    app.setGlobalPrefix("api");
    const { client } = reqlyNest(app, options(collector.url, { consumerHeader: "x-api-key", hashConsumer: false }));
    await app.listen(0, "127.0.0.1");
    const base = (await app.getUrl()).replace("[::1]", "127.0.0.1");

    await fetch(`${base}/api/users/7`, { headers: { "x-api-key": "acme" } });
    await fetch(`${base}/api/users/x/chat`, { method: "POST" });
    await fetch(`${base}/api/users/x/boom`);
    await fetch(`${base}/api/users/x/missing`);
    await fetch(`${base}/api/users/x/failing`);
    await fetch(`${base}/api/nope`);
    await app.close();
    await client.flush();

    const events = collector.events();
    assert.deepEqual(summary(events), [
      ["GET", "/api/users/:id", 200, false, null],
      ["POST", "/api/users/x/chat", 201, false, null],
      ["GET", "/api/users/x/boom", 500, true, "TypeError"],
      ["GET", "/api/users/x/missing", 404, false, null], // NotFoundException is not a server error
      ["GET", "/api/users/x/failing", 503, true, "HttpException"],
      ["GET", "__unmatched__", 404, false, null], // Nest's own catch-all 404
    ]);
    assert.equal(events[0].consumer_id, "acme");
    assert.deepEqual([events[1].llm_model, events[1].llm_input_tokens], ["gpt-4o-mini", 100]);
    await client.shutdown();
  });
}
