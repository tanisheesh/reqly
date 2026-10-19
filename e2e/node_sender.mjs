// Sends 25 requests' worth of events through the built Node SDK: one batch,
// large enough to go out gzipped. Used by e2e/test_e2e.py.
//   node e2e/node_sender.mjs <collector url> <ingest key> <service> <release>
import { ReqlyClient } from "../sdk-node/dist/esm/index.js";

const [collectorUrl, apiKey, serviceName, release] = process.argv.slice(2);
const client = new ReqlyClient({ collectorUrl, apiKey, serviceName, release, environment: "ci", flushIntervalMs: 60_000 });
for (let i = 0; i < 25; i++) {
  client.record({ method: "GET", route: "/items/:id", statusCode: i % 5 === 0 ? 500 : 200, durationMs: 5 + i });
}
await client.shutdown();
if (client.stats.shipped !== 25) {
  console.error("node sender: shipped", client.stats);
  process.exit(1);
}
