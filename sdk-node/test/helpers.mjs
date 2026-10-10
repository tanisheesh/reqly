import { createServer } from "node:http";
import { createHmac } from "node:crypto";

/** A collector stand-in. `statuses` scripts its responses (then 202s). */
export async function fakeCollector(statuses = []) {
  const batches = [];
  const requests = [];
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      requests.push({ url: req.url, headers: req.headers });
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
