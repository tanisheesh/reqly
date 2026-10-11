import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

import { ReqlyClient, recordLlmUsage } from "../dist/esm/index.js";
import { fakeCollector, options } from "./helpers.mjs";

const request = (extra = {}) => ({ method: "get", route: "/x", statusCode: 200, durationMs: 1.234, ...extra });

test("batches carry the release and environment; events follow the ingest spec", async () => {
  const collector = await fakeCollector();
  const client = new ReqlyClient(options(collector.url, { release: "v9", environment: "staging" }));
  client.record(request({ route: undefined, statusCode: 503 }));
  await client.shutdown();
  const [batch] = collector.batches;
  assert.equal(batch.release, "v9");
  assert.equal(batch.environment, "staging");
  assert.match(batch.sdk_version, /^node-/);
  const [event] = batch.events;
  assert.equal(event.route, "__unmatched__");
  assert.equal(event.method, "GET");
  assert.equal(event.error, true); // 5xx
  assert.equal(event.duration_ms, 1.23);
  assert.match(event.event_id, /^[0-9a-f-]{36}$/);
  assert.ok(!Number.isNaN(Date.parse(event.timestamp)));
  await collector.close();
});

test("retries 429 and 5xx, drops on other 4xx", async () => {
  const collector = await fakeCollector([429, 503]);
  const client = new ReqlyClient(options(collector.url));
  client.record(request());
  await client.flush();
  assert.equal(collector.requests.length, 3);
  assert.equal(client.stats.shipped, 1);
  await collector.close();

  const rejecting = await fakeCollector([401]);
  const client2 = new ReqlyClient(options(rejecting.url));
  client2.record(request());
  await client2.flush();
  assert.equal(rejecting.requests.length, 1); // not retried
  assert.equal(client2.stats.dropped, 1);
  await rejecting.close();
  await client.shutdown();
  await client2.shutdown();
});

test("an unreachable collector never throws into the app", async () => {
  const client = new ReqlyClient(options("http://127.0.0.1:1"));
  client.record(request());
  await client.flush();
  assert.equal(client.stats.dropped, 1);
  await client.shutdown();
});

test("queue is bounded and full batches flush on their own", async () => {
  const collector = await fakeCollector([503, 503, 503]);
  const client = new ReqlyClient(options(collector.url, { maxQueueSize: 3, maxBatchSize: 100 }));
  for (let i = 0; i < 5; i++) client.record(request({ route: `/r${i}` }));
  assert.equal(client.stats.dropped, 2); // oldest two dropped
  await client.shutdown();
  assert.deepEqual(collector.events(), []); // the one batch failed 3 times
  await collector.close();

  const c2 = await fakeCollector();
  const client2 = new ReqlyClient(options(c2.url, { maxBatchSize: 2 }));
  client2.record(request());
  client2.record(request()); // reaches the batch size: sent without waiting for the timer
  await new Promise((r) => setTimeout(r, 100));
  assert.equal(c2.events().length, 2);
  await client2.shutdown();
  await c2.close();
});

test("ignored routes, sampling and env configuration", async () => {
  const collector = await fakeCollector();
  process.env.REQLY_SERVICE_NAME = "from-env";
  process.env.GITHUB_SHA = "abc123";
  try {
    const client = new ReqlyClient({ collectorUrl: collector.url, flushIntervalMs: 60_000, sampleRate: 0 });
    assert.equal(client.config.serviceName, "from-env");
    assert.equal(client.config.release, "abc123");
    client.record(request());
    client.record(request({ route: "/health" }));
    await client.shutdown();
    assert.equal(client.stats.recorded, 0);
    assert.equal(client.stats.observed, 2); // seen, then sampled out / ignored
  } finally {
    delete process.env.REQLY_SERVICE_NAME;
    delete process.env.GITHUB_SHA;
  }
  const client = new ReqlyClient(options(collector.url));
  client.record(request({ route: "/health" }));
  assert.equal(client.stats.recorded, 0);
  await client.shutdown();
  await collector.close();
});

test("a throwing consumer function costs only the consumer id", async () => {
  const collector = await fakeCollector();
  const client = new ReqlyClient(options(collector.url, { consumer: () => { throw new Error("x"); }, consumerSalt: "s" }));
  client.record(request({ requestInfo: () => ({ method: "GET", path: "/x", headers: {}, raw: null }) }));
  await client.shutdown();
  assert.equal(collector.events()[0].consumer_id, null);
  await collector.close();
});

test("recordLlmUsage outside a request is a no-op", () => {
  recordLlmUsage("gpt-4o", 1, 1);
  recordLlmUsage("gpt-4o", -1, 0); // invalid, logged, not thrown
});

test("the CommonJS build loads with require()", () => {
  const require = createRequire(import.meta.url);
  const cjs = require("../dist/cjs/index.js");
  assert.equal(typeof cjs.reqlyExpress, "function");
  assert.equal(typeof cjs.ReqlyClient, "function");
});

test("queued events are sent when the process exits on its own", async () => {
  const collector = await fakeCollector();
  const { spawn } = await import("node:child_process");
  const script = `
    import { ReqlyClient } from ${JSON.stringify(new URL("../dist/esm/index.js", import.meta.url).href)};
    const client = new ReqlyClient({ serviceName: "exit-test", collectorUrl: ${JSON.stringify(collector.url)}, flushIntervalMs: 60000 });
    client.record({ method: "GET", route: "/bye", statusCode: 200, durationMs: 1 });
    // no shutdown(): the script just ends
  `;
  const child = spawn(process.execPath, ["--input-type=module", "-e", script], { stdio: "inherit" });
  await new Promise((resolve) => child.on("exit", resolve));
  assert.deepEqual(collector.events().map((e) => e.route), ["/bye"]);
  await collector.close();
});

test("events that arrive while a flush is sending wait for the next batch", async () => {
  const collector = await fakeCollector();
  const client = new ReqlyClient(options(collector.url, { maxBatchSize: 5 }));
  for (let i = 0; i < 5; i++) client.record(request({ route: `/a${i}` })); // reaches the batch size: flush starts
  // requests keep arriving while the batch is in flight
  for (let i = 0; i < 4; i++) {
    await new Promise((r) => setImmediate(r));
    client.record(request({ route: `/b${i}` }));
  }
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(collector.batches.length, 1); // not one HTTP call per late event
  assert.equal(collector.batches[0].events.length, 5);
  await client.shutdown(); // the 4 late ones go in one more batch
  assert.deepEqual(collector.batches.map((b) => b.events.length), [5, 4]);
  await collector.close();
});

test("shutdown during an in-flight flush still sends everything", async () => {
  const collector = await fakeCollector();
  const client = new ReqlyClient(options(collector.url, { maxBatchSize: 10 }));
  for (let i = 0; i < 10; i++) client.record(request()); // batch size reached: a flush is now in flight
  for (let i = 0; i < 25; i++) client.record(request()); // queued behind it
  await client.shutdown();
  assert.equal(collector.events().length, 35);
  assert.equal(client.stats.shipped, 35);
  await collector.close();
});


test("the flush interval reads either SDK's environment variable", () => {
  const interval = () => new ReqlyClient({ collectorUrl: "http://127.0.0.1:9" });
  try {
    const a = interval();
    assert.equal(a.config.flushIntervalMs, 5000);
    void a.shutdown();
    process.env.REQLY_FLUSH_INTERVAL_SECONDS = "2.5"; // the Python SDK's name
    const b = interval();
    assert.equal(b.config.flushIntervalMs, 2500);
    void b.shutdown();
    process.env.REQLY_FLUSH_INTERVAL_MS = "1000"; // Node's own name wins
    const c = interval();
    assert.equal(c.config.flushIntervalMs, 1000);
    void c.shutdown();
  } finally {
    delete process.env.REQLY_FLUSH_INTERVAL_SECONDS;
    delete process.env.REQLY_FLUSH_INTERVAL_MS;
  }
});

test("out-of-range shipping settings are clamped (a batch size of 0 would loop forever)", async () => {
  const collector = await fakeCollector();
  const client = new ReqlyClient(options(collector.url, { maxBatchSize: 0, flushIntervalMs: 0, maxQueueSize: 0 }));
  assert.equal(client.config.maxBatchSize, 1);
  assert.equal(client.config.flushIntervalMs, 100);
  assert.equal(client.config.maxQueueSize, 1);
  client.record(request());
  await client.shutdown(); // returns instead of looping
  assert.equal(collector.events().length, 1);
  const big = new ReqlyClient(options(collector.url, { maxBatchSize: 5000 }));
  assert.equal(big.config.maxBatchSize, 1000);
  await big.shutdown();
  await collector.close();
});
