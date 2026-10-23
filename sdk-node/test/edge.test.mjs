import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, before, test } from "node:test";

import { normalizeEdgeRoute, reqlyFetch } from "../dist/esm/edge.js";
import { fakeCollector, hmac16 } from "./helpers.mjs";

let collector;
before(async () => {
  collector = await fakeCollector();
});
after(async () => {
  await collector.close();
});

/** A Workers-style ctx whose waitUntil promises we can await. */
function workerCtx() {
  const pending = [];
  return { pending, waitUntil: (p) => pending.push(p), settle: () => Promise.all(pending) };
}

test("Workers: events go out through ctx.waitUntil, after the response", async () => {
  collector.batches.length = 0;
  const handler = reqlyFetch(
    async (request) => (new URL(request.url).pathname === "/boom" ? new Response("no", { status: 503 }) : Response.json({ ok: 1 })),
    (env) => ({ serviceName: "worker", collectorUrl: collector.url, apiKey: env.KEY, environment: "production",
      route: (request) => new URL(request.url).pathname.replace(/\/\d+$/, "/:id"), consumerHeader: "x-api-key",
      consumerSalt: "s3cret" }),
  );
  const ctx = workerCtx();
  const response = await handler(new Request("https://api.example.com/orders/7", { headers: { "x-api-key": "key_1" } }), { KEY: "k" }, ctx);
  assert.equal(response.status, 200);
  await handler(new Request("https://api.example.com/boom", {
    headers: { traceparent: "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" } }), { KEY: "k" }, ctx);
  assert.equal(ctx.pending.length, 2);
  await ctx.settle();
  // the two sends run concurrently: order by route
  const [boom, ok] = collector.events().sort((a, b) => a.route.localeCompare(b.route));
  assert.ok(collector.batches.every((b) => b.environment === "production"));
  assert.match(collector.batches.at(-1).sdk_version, /^node-edge-/);
  assert.deepEqual([ok.method, ok.route, ok.status_code, ok.error, ok.host], ["GET", "/orders/:id", 200, false, "api.example.com"]);
  assert.equal(ok.consumer_id, hmac16("key_1", "s3cret")); // same id as the Node SDK gives
  assert.deepEqual([boom.status_code, boom.error, boom.trace_id], [503, true, "4bf92f3577b34da6a3ce929d0e0e4736"]);
  assert.equal(collector.requests.at(-1).headers["x-reqly-key"], "k");
  assert.equal(handler.client().stats.shipped, 2);
});

test("Next.js: a route string with dynamic segments, a thrown error, and the waitUntil option", async () => {
  collector.batches.length = 0;
  const pending = [];
  const GET = reqlyFetch(
    async () => {
      throw new TypeError("bad order");
    },
    { serviceName: "web", collectorUrl: collector.url, route: "/api/orders/[id]/items/[...rest]", waitUntil: (p) => pending.push(p) },
  );
  await assert.rejects(GET(new Request("http://localhost/api/orders/1/items/a/b")), /bad order/);
  await Promise.all(pending);
  const [event] = collector.events();
  assert.deepEqual([event.route, event.status_code, event.error, event.error_type], ["/api/orders/{id}/items/{rest}", 500, true, "TypeError"]);
});

test("large batches are gzipped; without any waitUntil the send still runs", async () => {
  collector.batches.length = 0;
  collector.requests.length = 0;
  const handler = reqlyFetch(() => new Response("ok"), { serviceName: "bun", collectorUrl: collector.url, route: "/x" });
  const client = () => handler.client();
  for (let i = 0; i < 1; i++) await handler(new Request("http://localhost/x"));
  for (let i = 0; i < 25; i++) client().record(new Request("http://localhost/x"), new Response("ok"), 1);
  await client().flush();
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(collector.events().length, 26);
  assert.ok(collector.requests.some((r) => r.headers["content-encoding"] === "gzip"));
});

test("route segments from Next.js and Remix become {name}", () => {
  assert.equal(normalizeEdgeRoute("/blog/[[...slug]]"), "/blog/{slug}");
  assert.equal(normalizeEdgeRoute("/users/$userId/posts"), "/users/{userId}/posts");
  assert.equal(normalizeEdgeRoute("/plain"), "/plain");
});

test("the edge build imports no node: modules", () => {
  for (const file of ["../dist/esm/edge.js", "../dist/esm/traces.js", "../dist/cjs/edge.js", "../dist/cjs/traces.js"]) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    const imports = [...source.matchAll(/(?:from\s+|require\()\s*["']([^"']+)["']/g)].map((m) => m[1]);
    assert.deepEqual(imports.filter((m) => !m.startsWith("./")), [], `${file} imports ${imports}`);
  }
});
