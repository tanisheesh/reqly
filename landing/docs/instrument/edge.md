---
title: Serverless & edge
description: Instrument Cloudflare Workers, Vercel, Next.js route handlers, Deno Deploy and Bun with reqly-node/edge.
---

# Serverless & edge

Serverless and edge runtimes may freeze or end the process the moment a response is sent, so the regular Node SDK's background flush can't run there. `reqly-node/edge` sends each request's event right after its response, kept alive by the platform's `waitUntil`. It uses only web APIs (`fetch`, WebCrypto, `CompressionStream`), no `node:` modules, so it runs on Cloudflare Workers without `nodejs_compat`.

```bash
npm install reqly-node      # 0.6+
```

Wrap any fetch-style handler, `(request) => Response`, with `reqlyFetch`:

=== "Cloudflare Workers"

    ```js
    import { reqlyFetch } from "reqly-node/edge";

    async function handle(request, env, ctx) {
      return Response.json({ ok: true });
    }

    export default {
      // options may be a function of env, for secrets in bindings
      fetch: reqlyFetch(handle, (env) => ({
        serviceName: "edge-api",
        collectorUrl: env.REQLY_COLLECTOR_URL,
        apiKey: env.REQLY_API_KEY,
        route: (request) => new URL(request.url).pathname.replace(/\/\d+(?=\/|$)/g, "/:id"),
      })),
    };
    ```

    The event is sent with `ctx.waitUntil`.

=== "Next.js (App Router)"

    ```ts
    // app/api/orders/[id]/route.ts
    import { after } from "next/server";
    import { reqlyFetch } from "reqly-node/edge";

    async function getOrder(request: Request) {
      return Response.json({ id: 1 });
    }

    export const GET = reqlyFetch(getOrder, {
      serviceName: "web",
      route: "/api/orders/[id]",   // recorded as /api/orders/{id}
      waitUntil: after,
    });
    ```

    On Vercel the request context's `waitUntil` is found by itself, so `waitUntil: after` is only needed elsewhere. `[id]`, `[...slug]` and `[[...slug]]` become `{id}` and `{slug}`.

=== "Deno, Bun, Netlify"

    ```js
    import { reqlyFetch } from "reqly-node/edge";

    const handler = reqlyFetch((request) => new Response("ok"), {
      serviceName: "api",
      route: (request) => new URL(request.url).pathname,
    });

    Deno.serve(handler);              // or Bun.serve({ fetch: handler }), or export default handler
    ```

    Long-running servers keep the send running on their own; no `waitUntil` needed.

## Options

The same as the Node SDK's where they apply. Each falls back to its `REQLY_*` environment variable where the runtime has `process.env` (Vercel, Netlify, Bun, Deno with `--allow-env`); on Workers pass them from `env`.

| Option | Default | Notes |
|---|---|---|
| `serviceName` | `REQLY_SERVICE_NAME` | |
| `collectorUrl` | `REQLY_COLLECTOR_URL` | |
| `apiKey` | `REQLY_API_KEY` | an ingest key, or a project key with the ingest scope |
| `route` | `__unmatched__` | a template string, or `(request, response) => string`. **Return a template, never the raw path** |
| `release` / `environment` | `REQLY_RELEASE`, `VERCEL_GIT_COMMIT_SHA`, `CF_PAGES_COMMIT_SHA`, … / `REQLY_ENVIRONMENT`, `VERCEL_ENV` | |
| `consumerHeader`, `consumer`, `consumerSalt`, `hashConsumer` | as the Node SDK | hashed with WebCrypto HMAC-SHA256: the same ids as the Node and Python SDKs |
| `sampleRate`, `traceSlowMs` | `1`, `500` | |
| `waitUntil` | `ctx.waitUntil`, else Vercel's | e.g. Next's `after`, or `waitUntil` from `@vercel/functions` |

## How it differs from the Node SDK

- **One send per request.** No batching across requests, since there's no "later" to batch for. A send is one attempt with a 3-second timeout; a failure drops that request's event.
- **Duration** is the time to the response headers; a streamed body may finish later.
- **No LLM usage per request.** `recordLlmUsage` needs `AsyncLocalStorage`, which edge runtimes don't all have. Record LLM calls from a Node runtime, or send [GenAI spans over OpenTelemetry](opentelemetry.md#llm-calls).
- Frameworks with their own adapters (Express, Fastify, Koa, NestJS, Hono on Node) should keep using the [Node SDK](node.md). Hono on Workers can wrap `app.fetch`: `export default { fetch: reqlyFetch(app.fetch, …) }`.
