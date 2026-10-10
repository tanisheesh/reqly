// How much time does reqly-node add to each request?
//
// Fastify (inject) and Hono (app.request) are driven in-process, without a
// network; Express has no in-process driver, so it is measured over
// loopback HTTP with a keep-alive agent (noisier). The instrumented apps
// ship to a local stand-in collector the whole time. Baseline and
// instrumented runs alternate in rounds so machine drift hits both.
//
//   npm run build && node scripts/bench.mjs [--requests 20000] [--json out.json]

import { createServer, Agent, request as httpRequest } from "node:http";
import { writeFileSync } from "node:fs";
import os from "node:os";

import express from "express";
import Fastify from "fastify";
import { Hono } from "hono";

import { reqlyExpress, reqlyFastify, reqlyHono, SDK_VERSION } from "../dist/esm/index.js";

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
};
const REQUESTS = Number(opt("requests", 20000));
const ROUNDS = Number(opt("rounds", 10));

// --- stand-in collector -----------------------------------------------------------
let shipped = 0;
const sink = createServer((req, res) => {
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    shipped += JSON.parse(body).events.length;
    res.writeHead(202).end("{}");
  });
});
await new Promise((r) => sink.listen(0, "127.0.0.1", r));
const collectorUrl = `http://127.0.0.1:${sink.address().port}`;
const reqlyOptions = { serviceName: "bench", collectorUrl, apiKey: "k", flushIntervalMs: 1000, maxQueueSize: 100000,
  consumerHeader: "x-api-key", consumerSalt: "bench" };

// --- drivers ------------------------------------------------------------------------
async function timed(n, one) {
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    const t = process.hrtime.bigint();
    await one();
    out[i] = Number(process.hrtime.bigint() - t);
  }
  return out;
}

function fastifyApp(plugin) {
  const app = Fastify();
  if (plugin) app.register(plugin);
  app.get("/items/:id", async (req) => ({ id: Number(req.params.id), name: "widget" }));
  return app;
}

function honoApp(middleware) {
  const app = new Hono();
  if (middleware) app.use(middleware);
  app.get("/items/:id", (c) => c.json({ id: Number(c.req.param("id")), name: "widget" }));
  return app;
}

async function expressServer(middleware) {
  const app = express();
  if (middleware) app.use(middleware);
  app.get("/items/:id", (req, res) => res.json({ id: Number(req.params.id), name: "widget" }));
  const server = app.listen(0, "127.0.0.1");
  await new Promise((r) => server.once("listening", r));
  const agent = new Agent({ keepAlive: true, maxSockets: 1 });
  const port = server.address().port;
  const get = () =>
    new Promise((resolve, reject) => {
      const req = httpRequest({ host: "127.0.0.1", port, path: "/items/42", agent, headers: { "x-api-key": "k1" } }, (res) => {
        res.resume();
        res.on("end", resolve);
      });
      req.on("error", reject);
      req.end();
    });
  return { get, close: () => { agent.destroy(); return new Promise((r) => server.close(r)); } };
}

async function variants(name) {
  if (name === "fastify") {
    const plugin = reqlyFastify(reqlyOptions);
    const base = fastifyApp(null);
    const inst = fastifyApp(plugin);
    await base.ready();
    await inst.ready();
    const call = (app) => () => app.inject({ method: "GET", url: "/items/42", headers: { "x-api-key": "k1" } });
    return { base: call(base), inst: call(inst), client: plugin.client, close: async () => { await base.close(); await inst.close(); } };
  }
  if (name === "hono") {
    const mw = reqlyHono(reqlyOptions);
    const base = honoApp(null);
    const inst = honoApp(mw);
    const call = (app) => () => app.request("/items/42", { headers: { "x-api-key": "k1" } });
    return { base: call(base), inst: call(inst), client: mw.client, close: async () => {} };
  }
  const mw = reqlyExpress(reqlyOptions);
  const base = await expressServer(null);
  const inst = await expressServer(mw);
  return { base: base.get, inst: inst.get, client: mw.client, close: async () => { await base.close(); await inst.close(); } };
}

// --- measurement --------------------------------------------------------------------
function summarize(ns) {
  const us = ns.map((v) => v / 1000).sort((a, b) => a - b);
  const mean = us.reduce((a, b) => a + b, 0) / us.length;
  const round2 = (v) => Math.round(v * 100) / 100;
  return { mean_us: round2(mean), p50_us: round2(us[Math.floor(us.length / 2)]), p99_us: round2(us[Math.floor(us.length * 0.99)]) };
}

const results = [];
for (const name of ["fastify", "hono", "express"]) {
  const v = await variants(name);
  const perRound = Math.max(1, Math.floor(REQUESTS / ROUNDS));
  await timed(500, v.base); // warm-up
  await timed(500, v.inst);
  let base = [];
  let inst = [];
  for (let r = 0; r < ROUNDS; r++) {
    base = base.concat(await timed(perRound, v.base));
    inst = inst.concat(await timed(perRound, v.inst));
  }
  await v.client.shutdown();
  await v.close();
  const st = v.client.stats;
  const b = summarize(base);
  const i = summarize(inst);
  results.push({
    framework: name,
    transport: name === "express" ? "loopback HTTP" : "in-process",
    requests: base.length,
    baseline: b,
    reqly: i,
    events: { recorded: st.recorded, shipped: st.shipped, dropped: st.dropped },
    added_us: { mean: Math.round((i.mean_us - b.mean_us) * 100) / 100, p50: Math.round((i.p50_us - b.p50_us) * 100) / 100,
      p99: Math.round((i.p99_us - b.p99_us) * 100) / 100 },
  });
}
sink.close();

const signed = (v) => (v >= 0 ? `+${v} µs` : `−${-v} µs (noise)`);
console.log(`reqly-node ${SDK_VERSION} overhead -- Node ${process.version}, ${os.platform()} ${os.arch()}`);
console.log(`${REQUESTS} requests per variant, ${ROUNDS} alternating rounds\n`);
console.log("| framework | driver | baseline p50 | with Reqly p50 | added p50 | added mean | added p99 |");
console.log("|---|---|---|---|---|---|---|");
for (const r of results) {
  console.log(`| ${r.framework} | ${r.transport} | ${r.baseline.p50_us} µs | ${r.reqly.p50_us} µs | **${signed(r.added_us.p50)}** | ` +
    `${signed(r.added_us.mean)} | ${signed(r.added_us.p99)} |`);
}
const total = (key) => results.reduce((n, r) => n + r.events[key], 0);
console.log(`\nEvents: recorded ${total("recorded")}, shipped ${total("shipped")}, dropped ${total("dropped")}; ` +
  `received by the stand-in collector ${shipped}`);
const jsonPath = opt("json", null);
if (jsonPath) {
  writeFileSync(jsonPath, JSON.stringify({ env: { node: process.version, platform: `${os.platform()} ${os.arch()}`, cpu: os.cpus()[0]?.model, reqly_node: SDK_VERSION }, results }, null, 2));
}
