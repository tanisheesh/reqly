import { createServer } from "node:http";
import { createHmac } from "node:crypto";
import { gunzipSync } from "node:zlib";

/**
 * A collector stand-in. `statuses` scripts its responses (then 202s).
 * `gzip: false` answers gzip bodies with 422, like a collector older than 0.13.
 */
export async function fakeCollector(statuses = [], { gzip = true } = {}) {
  const batches = [];
  const requests = [];
  const specs = []; // OpenAPI uploads: { url, spec }
  const server = createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      requests.push({ url: req.url, headers: req.headers });
      const gzipped = req.headers["content-encoding"] === "gzip";
      if (gzipped && !gzip) {
        res.writeHead(422, { "Content-Type": "application/json" });
        return res.end("{}");
      }
      const raw = Buffer.concat(chunks);
      const body = (gzipped ? gunzipSync(raw) : raw).toString();
      if (req.method === "PUT" && req.url.endsWith("/openapi")) {
        specs.push({ url: req.url, spec: JSON.parse(body) });
        res.writeHead(200, { "Content-Type": "application/json" });
        return res.end("{}");
      }
      const status = statuses.length ? statuses.shift() : 202;
      if (status < 400) batches.push(JSON.parse(body));
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end("{}");
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  return {
    url,
    batches,
    requests,
    specs,
    events: () => batches.flatMap((b) => b.events),
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

export function hmac16(value, salt) {
  return createHmac("sha256", salt).update(value).digest("hex").slice(0, 16);
}

export const options = (url, extra = {}) => ({
  serviceName: "node-test",
  collectorUrl: url,
  apiKey: "k",
  flushIntervalMs: 60_000,
  ...extra,
});
