/**
 * Reqly for serverless and edge runtimes: Cloudflare Workers, Vercel (Edge
 * and Node functions), Next.js route handlers, Deno Deploy, Netlify Edge,
 * Bun and anything else with fetch-style `(request) => Response` handlers.
 *
 *   import { reqlyFetch } from "reqly-node/edge";
 *
 *   // Cloudflare Workers
 *   export default { fetch: reqlyFetch(handler, (env) => ({ serviceName: "api", apiKey: env.REQLY_API_KEY })) };
 *
 *   // Next.js App Router (app/api/orders/[id]/route.ts)
 *   export const GET = reqlyFetch(getOrder, { serviceName: "web", route: "/api/orders/[id]" });
 *
 * Such runtimes may freeze or end the process as soon as the response is
 * sent, so there is no background timer: each request's events are sent
 * right after it, kept alive with the platform's waitUntil (the Workers
 * `ctx`, Vercel's request context, or the `waitUntil` option, e.g. Next's
 * `after`). Uses only web APIs (fetch, WebCrypto, CompressionStream), no
 * `node:` modules. LLM usage isn't tied to requests here; record it from a
 * Node runtime, or send GenAI spans over OpenTelemetry.
 */

import { traceIdFromHeaders, TRACE_SLOW_MS, wantsTraceId } from "./traces.js";

export const EDGE_SDK_VERSION = "0.6.0";

const UNMATCHED = "__unmatched__";
const REQUEST_TIMEOUT_MS = 3000;
const MAX_BATCH = 1000;
const GZIP_MIN_BYTES = 1024;
const GZIP_REJECTED = new Set([400, 415, 422]);
const RELEASE_ENV_VARS = [
  "REQLY_RELEASE", "VERCEL_GIT_COMMIT_SHA", "CF_PAGES_COMMIT_SHA", "GITHUB_SHA", "RENDER_GIT_COMMIT",
  "COMMIT_REF", "DENO_DEPLOYMENT_ID", "SOURCE_VERSION",
];

export interface EdgeRequestInfo {
  method: string;
  path: string;
  /** Lower-case header names. */
  headers: Record<string, string | undefined>;
  raw: Request;
}

export interface ReqlyEdgeOptions {
  serviceName?: string;
  collectorUrl?: string;
  apiKey?: string;
  release?: string;
  environment?: string;
  /**
   * The route template: a string (one handler per route, as in Next.js:
   * "/api/orders/[id]" becomes "/api/orders/{id}"), or a function of the
   * request and response. Without it every request is `__unmatched__`.
   */
  route?: string | ((request: Request, response: Response | undefined) => string | undefined);
  consumerHeader?: string;
  consumer?: (info: EdgeRequestInfo) => string | null | undefined;
  consumerSalt?: string;
  hashConsumer?: boolean;
  sampleRate?: number;
  traceSlowMs?: number;
  /** Keeps the send alive after the response, e.g. Next's `after` or `@vercel/functions` waitUntil. */
  waitUntil?: (promise: Promise<unknown>) => void;
}

interface WaitUntilContext {
  waitUntil?: (promise: Promise<unknown>) => void;
}

type Handler<E, C> = (request: Request, env: E, ctx: C) => Response | Promise<Response>;

interface WireEvent {
  event_id: string;
  timestamp: string;
  method: string;
  route: string;
  status_code: number;
  duration_ms: number;
  error: boolean;
  error_type: string | null;
  host: string | null;
  request_bytes: number | null;
  response_bytes: number | null;
  consumer_id: string | null;
  trace_id?: string;
}

function processEnv(name: string): string | undefined {
  const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env;
  const value = env?.[name];
  return value === undefined || value === "" ? undefined : value;
}

/** Next.js / Remix-style segments ([id], [...slug], [[...slug]], $id) as {name}. */
export function normalizeEdgeRoute(route: string): string {
  return route
    .replace(/\[\[?\.\.\.([^\]]+)\]\]?/g, "{$1}")
    .replace(/\[([^\]]+)\]/g, "{$1}")
    .replace(/\/\$([A-Za-z_]\w*)/g, "/{$1}")
    .slice(0, 512);
}

function headerRecord(headers: Headers): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  headers.forEach((value, key) => {
    out[key.toLowerCase()] = value;
  });
  return out;
}

async function hmac16(value: string, salt: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", encoder.encode(salt), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(value)));
  return Array.from(signature.slice(0, 8), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function gzip(body: string): Promise<Uint8Array | undefined> {
  if (typeof CompressionStream === "undefined") return undefined;
  const stream = new Blob([body]).stream().pipeThrough(new CompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** One service's events, sent after each request. Never throws into the app. */
export class ReqlyEdgeClient {
  private readonly options: Required<Pick<ReqlyEdgeOptions, "serviceName" | "collectorUrl" | "hashConsumer" | "sampleRate" | "traceSlowMs">> &
    ReqlyEdgeOptions;
  private queue: Promise<WireEvent | undefined>[] = [];
  private gzipOk = true;
  private warned = false;
  readonly stats = { recorded: 0, shipped: 0, dropped: 0 };

  constructor(options: ReqlyEdgeOptions = {}) {
    this.options = {
      ...options,
      serviceName: options.serviceName ?? processEnv("REQLY_SERVICE_NAME") ?? "unnamed-service",
      collectorUrl: (options.collectorUrl ?? processEnv("REQLY_COLLECTOR_URL") ?? "http://localhost:8000").replace(/\/+$/, ""),
      apiKey: options.apiKey ?? processEnv("REQLY_API_KEY"),
      release: (options.release ?? RELEASE_ENV_VARS.map(processEnv).find((v) => v !== undefined))?.slice(0, 128),
      environment: (options.environment ?? processEnv("REQLY_ENVIRONMENT") ?? processEnv("VERCEL_ENV"))?.slice(0, 32),
      consumerSalt: options.consumerSalt ?? processEnv("REQLY_CONSUMER_SALT"),
      hashConsumer: options.hashConsumer ?? true,
      sampleRate: Math.min(1, Math.max(0, options.sampleRate ?? 1)),
      traceSlowMs: options.traceSlowMs ?? TRACE_SLOW_MS,
    };
  }

  /** Queues one request's event. */
  record(request: Request, response: Response | undefined, durationMs: number, errorType?: string): void {
    try {
      if (this.options.sampleRate < 1 && Math.random() >= this.options.sampleRate) return;
      this.queue.push(this.build(request, response, durationMs, errorType).catch(() => undefined));
      this.stats.recorded += 1;
    } catch {
      // never into the app
    }
  }

  private async build(request: Request, response: Response | undefined, durationMs: number, errorType?: string): Promise<WireEvent> {
    const status = response?.status ?? 500;
    const url = new URL(request.url);
    const headers = headerRecord(request.headers);
    const route = typeof this.options.route === "function" ? this.options.route(request, response) : this.options.route;
    const event: WireEvent = {
      event_id: crypto.randomUUID(),
      timestamp: new Date().toISOString(),
      method: request.method.toUpperCase().slice(0, 16),
      route: route ? normalizeEdgeRoute(route) : UNMATCHED,
      status_code: status,
      duration_ms: Math.round(durationMs * 100) / 100,
      error: errorType !== undefined || status >= 500,
      error_type: errorType ?? null,
      host: url.host || null,
      request_bytes: Number(headers["content-length"]) || null,
      response_bytes: Number(response?.headers.get("content-length")) || null,
      consumer_id: await this.consumerId(request, url, headers),
    };
    if (wantsTraceId(status, durationMs, this.options.traceSlowMs)) {
      const traceId = traceIdFromHeaders(headers);
      if (traceId) event.trace_id = traceId;
    }
    return event;
  }

  private async consumerId(request: Request, url: URL, headers: Record<string, string | undefined>): Promise<string | null> {
    const { consumerHeader, consumer, consumerSalt, hashConsumer } = this.options;
    if (!consumerHeader && !consumer) return null;
    try {
      const value = consumer
        ? consumer({ method: request.method, path: url.pathname, headers, raw: request })
        : headers[consumerHeader!.toLowerCase()];
      if (value === undefined || value === null || value === "") return null;
      return hashConsumer ? await hmac16(String(value), consumerSalt ?? "") : String(value).slice(0, 128);
    } catch {
      return null; // a failing consumer function costs the id, not the event
    }
  }

  /** Sends everything queued. Safe to call any time; resolves when done, never rejects. */
  async flush(): Promise<void> {
    const pending = this.queue.splice(0);
    if (pending.length === 0) return;
    const events = (await Promise.all(pending)).filter((e): e is WireEvent => e !== undefined);
    for (let i = 0; i < events.length; i += MAX_BATCH) {
      const batch = events.slice(i, i + MAX_BATCH);
      if (await this.send(batch)) this.stats.shipped += batch.length;
      else this.stats.dropped += batch.length;
    }
  }

  private async send(events: WireEvent[]): Promise<boolean> {
    const body = JSON.stringify({
      service_name: this.options.serviceName,
      sdk_version: `node-edge-${EDGE_SDK_VERSION}`,
      ...(this.options.release ? { release: this.options.release } : {}),
      ...(this.options.environment ? { environment: this.options.environment } : {}),
      events,
    });
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.options.apiKey) headers["X-Reqly-Key"] = this.options.apiKey;
    const url = `${this.options.collectorUrl}/v1/ingest`;
    try {
      let compressed: Uint8Array | undefined;
      if (this.gzipOk && body.length >= GZIP_MIN_BYTES) compressed = await gzip(body).catch(() => undefined);
      let response: Response;
      if (compressed) {
        response = await fetch(url, {
          method: "POST",
          headers: { ...headers, "Content-Encoding": "gzip" },
          body: compressed,
          signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
        });
        if (GZIP_REJECTED.has(response.status)) {
          response = await fetch(url, { method: "POST", headers, body, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
          if (response.ok) this.gzipOk = false; // the collector can't read gzip
        }
      } else {
        response = await fetch(url, { method: "POST", headers, body, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
      }
      if (!response.ok && !this.warned) {
        this.warned = true;
        console.warn(`reqly: collector rejected a batch with ${response.status}`);
      }
      return response.ok;
    } catch {
      return false; // one attempt: the runtime may not wait for retries
    }
  }
}

function vercelWaitUntil(): ((promise: Promise<unknown>) => void) | undefined {
  const context = (globalThis as Record<symbol, { get?: () => WaitUntilContext | undefined } | undefined>)[
    Symbol.for("@vercel/request-context")
  ];
  return context?.get?.()?.waitUntil;
}

/**
 * Wraps a fetch-style handler: records each request and sends it after the
 * response. `options` may be a function of the Workers `env`, for bindings.
 */
export function reqlyFetch<E = unknown, C = unknown>(
  handler: Handler<E, C>,
  options: ReqlyEdgeOptions | ((env: E) => ReqlyEdgeOptions) = {},
): Handler<E, C> & { client: () => ReqlyEdgeClient | undefined } {
  let client: ReqlyEdgeClient | undefined;
  const wrapped = async (request: Request, env: E, ctx: C): Promise<Response> => {
    try {
      client ??= new ReqlyEdgeClient(typeof options === "function" ? options(env) : options);
    } catch {
      return handler(request, env, ctx); // misconfigured: the app still answers
    }
    const start = performance.now();
    let response: Response | undefined;
    let errorType: string | undefined;
    try {
      response = await handler(request, env, ctx);
      return response;
    } catch (err) {
      errorType = (err as Error)?.name || "Error";
      throw err;
    } finally {
      const active = client;
      active.record(request, response, performance.now() - start, errorType);
      const sending = active.flush();
      const waitUntil =
        (ctx as WaitUntilContext | undefined)?.waitUntil?.bind(ctx) ??
        (typeof options === "function" ? undefined : options.waitUntil) ??
        vercelWaitUntil();
      if (waitUntil) {
        try {
          waitUntil(sending);
        } catch {
          // a platform that refuses: the send still runs
        }
      }
    }
  };
  return Object.assign(wrapped, { client: () => client });
}
