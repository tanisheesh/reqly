// Smoke test on Bun (run with `bun test/bun-smoke.mjs`): Hono served by
// Bun.serve and a node:http handler, both reporting to a stand-in collector.
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { Hono } from "hono";

import { reqlyFetch } from "../dist/esm/edge.js";
import { recordLlmUsage, reqlyHono, reqlyHttp } from "../dist/esm/index.js";
import { fakeCollector, options } from "./helpers.mjs";

if (typeof Bun === "undefined") throw new Error("run this with bun");

const collector = await fakeCollector();
const summary = () => collector.events().map((e) => [e.method, e.route, e.status_code, e.error]);

// Hono on Bun.serve
const app = new Hono();
const reqly = reqlyHono(options(collector.url));
app.use(reqly);
app.get("/users/:id", (c) => c.json({ id: c.req.param("id") }));
app.post("/chat", async (c) => {
  await new Promise((r) => setTimeout(r, 5));
  recordLlmUsage("gpt-4o-mini", 100, 20); // AsyncLocalStorage across an await
  return c.text("ok", 201);
});
app.get("/boom", () => {
  throw new TypeError("bad input");
});
const server = Bun.serve({ port: 0, fetch: app.fetch });
const base = `http://127.0.0.1:${server.port}`;
await fetch(`${base}/users/7`);
await fetch(`${base}/chat`, { method: "POST" });
await fetch(`${base}/boom`);
await fetch(`${base}/nope`);
server.stop(true);
await reqly.client.flush();
assert.deepEqual(summary(), [
  ["GET", "/users/:id", 200, false],
  ["POST", "/chat", 201, false],
  ["GET", "/boom", 500, true],
  ["GET", "__unmatched__", 404, false],
]);
assert.equal(collector.events()[1].llm_input_tokens, 100);
await reqly.client.shutdown();

// reqlyHttp on Bun's node:http
collector.batches.length = 0;
const handler = reqlyHttp((req, res) => res.end("ok"), { ...options(collector.url), routeResolver: () => "/ping" });
const httpServer = createServer(handler);
await new Promise((r) => httpServer.listen(0, "127.0.0.1", r));
await fetch(`http://127.0.0.1:${httpServer.address().port}/ping`);
await new Promise((r) => httpServer.close(r));
await handler.client.flush();
assert.deepEqual(summary(), [["GET", "/ping", 200, false]]);
await handler.client.shutdown();

// reqly-node/edge: a fetch handler on Bun.serve
collector.batches.length = 0;
const edge = reqlyFetch(() => new Response("edge"), { serviceName: "bun-edge", collectorUrl: collector.url, route: "/items/[id]" });
const pending = [];
const edgeServer = Bun.serve({ port: 0, fetch: (request) => edge(request, undefined, { waitUntil: (p) => pending.push(p) }) });
await fetch(`http://localhost:${edgeServer.port}/items/3`);
edgeServer.stop(true);
await Promise.all(pending);
assert.deepEqual(summary(), [["GET", "/items/{id}", 200, false]]);

await collector.close();
console.log(`bun ${Bun.version}: hono + node:http + edge OK`);
