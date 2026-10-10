// Manual end-to-end check against a running collector (not part of `npm test`):
//   npm run build && REQLY_COLLECTOR_URL=http://localhost:8000 REQLY_API_KEY=demo-key node scripts/e2e-local.mjs
import express from "express";

import { recordLlmUsage, reqlyExpress } from "../dist/esm/index.js";

const reqly = reqlyExpress({ serviceName: "node-e2e", consumerHeader: "x-api-key", consumerSalt: "e2e-salt" });
const app = express();
app.use(reqly);
app.get("/users/:id", (req, res) => res.json({ id: req.params.id }));
app.post("/chat", (req, res) => {
  recordLlmUsage("gpt-4o-mini", 1500, 300);
  res.send("ok");
});

const server = app.listen(0);
const base = `http://127.0.0.1:${server.address().port}`;
for (let i = 0; i < 20; i++) {
  await fetch(`${base}/users/${i}`, { headers: { "x-api-key": i % 4 ? "key-a" : "key-b" } });
  await fetch(`${base}/chat`, { method: "POST", headers: { "x-api-key": "key-a" } });
}
await fetch(`${base}/missing`);
server.close();
await reqly.client.shutdown();
console.log(JSON.stringify(reqly.client.stats));
