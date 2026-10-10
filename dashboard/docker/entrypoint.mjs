// Container entrypoint: writes dist/config.js from the environment, then
// serves the built dashboard. Lets one published image point at any
// collector:
//   docker run -e REQLY_COLLECTOR_URL=https://reqly.example.com -e REQLY_READ_KEY=... ghcr.io/tanisheesh/reqly-dashboard
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const config = {};
if (process.env.REQLY_COLLECTOR_URL) config.collectorUrl = process.env.REQLY_COLLECTOR_URL;
if (process.env.REQLY_READ_KEY) config.readKey = process.env.REQLY_READ_KEY;
// JSON.stringify escapes quotes; "<" is escaped too so a value can't close a script tag.
const json = JSON.stringify(config).replace(/</g, "\\u003c");
writeFileSync("/app/dist/config.js", `window.__REQLY_CONFIG__ = ${json};\n`);

const port = process.env.PORT || "5173";
const server = spawn("serve", ["-s", "dist", "-l", port], { stdio: "inherit" });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.kill(signal));
server.on("exit", (code) => process.exit(code ?? 0));
