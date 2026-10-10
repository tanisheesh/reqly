import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import fastifySwagger from "@fastify/swagger";
import express from "express";
import Fastify from "fastify";

import { reqlyExpress, reqlyFastify } from "../dist/esm/index.js";
import { fakeCollector, options } from "./helpers.mjs";

let collector;
before(async () => {
  collector = await fakeCollector();
});
after(async () => {
  await collector.close();
});

async function until(check, ms = 2000) {
  const end = Date.now() + ms;
  while (!check() && Date.now() < end) await new Promise((r) => setTimeout(r, 10));
}

test("fastify: pushOpenapi true uploads the @fastify/swagger spec once", async () => {
  collector.specs.length = 0;
  const app = Fastify();
  await app.register(fastifySwagger, { openapi: { info: { title: "shop", version: "1.0.0" } } });
  const plugin = reqlyFastify(options(collector.url, { pushOpenapi: true }));
  await app.register(plugin);
  app.get("/users/:id", async (req) => ({ id: req.params.id }));
  await app.ready();
  await app.inject({ url: "/users/1" });
  await app.inject({ url: "/users/2" });
  await until(() => collector.specs.length > 0);
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(collector.specs.length, 1); // once, not per request
  assert.equal(collector.specs[0].url, "/v1/services/node-test/openapi");
  assert.equal(collector.specs[0].spec.info.title, "shop");
  assert.ok(collector.specs[0].spec.paths["/users/{id}"]);
  await plugin.client.shutdown();
  await app.close();
});

test("express: pushOpenapi takes the spec object (or a function returning it)", async () => {
  collector.specs.length = 0;
  const spec = { openapi: "3.0.0", info: { title: "orders", version: "2" }, paths: { "/orders": { post: {} } } };
  const app = express();
  const reqly = reqlyExpress(options(collector.url, { pushOpenapi: async () => spec }));
  app.use(reqly);
  app.post("/orders", (req, res) => res.status(201).end());
  const server = await new Promise((resolve) => {
    const s = app.listen(0, "127.0.0.1", () => resolve(s));
  });
  await fetch(`http://127.0.0.1:${server.address().port}/orders`, { method: "POST" });
  await new Promise((r) => server.close(r));
  await until(() => collector.specs.length > 0);
  assert.deepEqual(collector.specs[0].spec, spec);
  await reqly.client.shutdown();
});

test("pushOpenapi true without a spec source warns and uploads nothing", async () => {
  collector.specs.length = 0;
  const warnings = [];
  const warn = console.warn;
  console.warn = (...args) => warnings.push(args.join(" "));
  try {
    const app = express();
    const reqly = reqlyExpress(options(collector.url, { pushOpenapi: true }));
    app.use(reqly);
    app.get("/x", (req, res) => res.end());
    const server = await new Promise((resolve) => {
      const s = app.listen(0, "127.0.0.1", () => resolve(s));
    });
    await fetch(`http://127.0.0.1:${server.address().port}/x`);
    await new Promise((r) => server.close(r));
    await until(() => warnings.some((w) => w.includes("pushOpenapi")));
    await reqly.client.shutdown();
  } finally {
    console.warn = warn;
  }
  assert.ok(warnings.some((w) => w.includes("pushOpenapi needs a spec")));
  assert.equal(collector.specs.length, 0);
});
