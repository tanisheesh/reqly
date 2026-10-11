import { createHmac, randomUUID } from "node:crypto";
import { hostname } from "node:os";

import { ReqlyOptions, ResolvedConfig, resolveConfig } from "./config.js";
import { LlmSummary, RequestInfo } from "./context.js";

export const SDK_VERSION = "0.3.2";
export const UNMATCHED_ROUTE = "__unmatched__";

const HOST = hostname();
const MAX_RETRIES = 3;
const REQUEST_TIMEOUT_MS = 3000;
/** Longest `shutdown()` may take: a down collector must not hold up a deploy. */
const SHUTDOWN_FLUSH_MS = 5000;

export interface RecordedRequest {
  method: string;
  /** The route template ("/users/:id"), or undefined when no route matched. */
  route: string | undefined;
  statusCode: number;
  durationMs: number;
  errorType?: string;
  requestBytes?: number;
  responseBytes?: number;
  requestInfo?: () => RequestInfo;
  llm?: LlmSummary;
}

interface WireEvent {
  event_id: string;
  timestamp: string;
  method: string;
  route: string;
  status_code: number;
  duration_ms: number;
  error: boolean;
  error_type: string | null;
  host: string;
  request_bytes: number | null;
  response_bytes: number | null;
  consumer_id: string | null;
  llm_model: string | null;
  llm_input_tokens: number | null;
  llm_output_tokens: number | null;
}

// Clients with queued events are flushed when the process is about to exit
// on its own (the Python SDK's atexit). One listener for all clients.
// beforeExit doesn't fire on process.exit() or a signal: call shutdown().
const liveClients = new Set<ReqlyClient>();
let exitHookInstalled = false;

function installExitHook(): void {
  if (exitHookInstalled) return;
  exitHookInstalled = true;
  // The event loop is empty, so the process is about to exit: send what is
  // queued the bounded way (shutdown), not with a flush's retries.
  process.on("beforeExit", () => {
    for (const client of liveClients) {
      if (client.hasQueued()) void client.shutdown();
    }
  });
}

function retryable(status: number): boolean {
  return status === 408 || status === 429 || status >= 500;
}

/**
 * One instrumented app's buffer and shipper. Every public method is
 * fail-open: an internal error disables instrumentation (logged once)
 * instead of reaching the app's request path.
 */
export class ReqlyClient {
  readonly config: ResolvedConfig;
  private queue: WireEvent[] = [];
  private timer: NodeJS.Timeout | undefined;
  private flushing: Promise<void> | undefined;
  private disabled = false;
  private stopping = false; // shutdown() started: flushes stop retrying
  // The same few API keys / tenants call over and over: hash each once.
  private readonly hashedConsumers = new Map<string, string>();
  private openapiSource: (() => unknown) | undefined;
  private openapiPushed = false;
  /**
   * observed: requests seen (before sampling and ignored routes); recorded:
   * the ones queued; shipped / dropped: events sent / lost; failedBatches:
   * batches given up after retries.
   */
  readonly stats = { observed: 0, recorded: 0, shipped: 0, dropped: 0, failedBatches: 0 };

  constructor(options: ReqlyOptions = {}) {
    this.config = resolveConfig(options);
    if (this.config.hashConsumer && (this.config.consumerHeader || this.config.consumer) && !this.config.consumerSalt) {
      console.warn(
        "reqly: consumer tracking without REQLY_CONSUMER_SALT -- hashed ids of guessable values " +
          "(user ids, emails) can be reversed by trying candidates; set a secret salt",
      );
    }
    const spec = this.config.pushOpenapi;
    if (typeof spec === "function") this.openapiSource = spec as () => unknown;
    else if (spec && typeof spec === "object") this.openapiSource = () => spec;
    this.timer = setInterval(() => void this.flush(), this.config.flushIntervalMs);
    this.timer.unref(); // never keeps the process alive
    liveClients.add(this);
    installExitHook();
  }

  /** True while events are waiting to be sent. */
  hasQueued(): boolean {
    return this.queue.length > 0;
  }

  /** Where `pushOpenapi: true` gets the spec from (set by a framework integration). */
  useOpenapiSource(source: () => unknown): void {
    if (this.config.pushOpenapi === true && !this.openapiSource) this.openapiSource = source;
  }

  record(request: RecordedRequest): void {
    if (this.disabled) return;
    try {
      if (!this.openapiPushed && this.config.pushOpenapi) {
        this.openapiPushed = true;
        void this.pushOpenapi();
      }
      this.stats.observed += 1;
      const route = request.route || UNMATCHED_ROUTE;
      if (this.config.ignoreRoutes.has(route)) return;
      if (this.config.sampleRate < 1 && Math.random() >= this.config.sampleRate) return;
      const error = request.errorType !== undefined || request.statusCode >= 500;
      this.queue.push({
        event_id: randomUUID(),
        timestamp: new Date().toISOString(),
        method: request.method.toUpperCase(),
        route: route.slice(0, 512),
        status_code: request.statusCode,
        duration_ms: Math.round(request.durationMs * 100) / 100,
        error,
        error_type: request.errorType ?? null,
        host: HOST,
        request_bytes: request.requestBytes ?? null,
        response_bytes: request.responseBytes ?? null,
        consumer_id: this.consumerId(request.requestInfo),
        llm_model: request.llm?.model ?? null,
        llm_input_tokens: request.llm?.inputTokens ?? null,
        llm_output_tokens: request.llm?.outputTokens ?? null,
      });
      this.stats.recorded += 1;
      if (this.queue.length > this.config.maxQueueSize) {
        const excess = this.queue.length - this.config.maxQueueSize;
        this.queue.splice(0, excess); // drop the oldest
        this.stats.dropped += excess;
      }
      if (this.queue.length >= this.config.maxBatchSize) void this.flush();
    } catch (err) {
      this.disabled = true;
      console.warn("reqly: internal error, disabling instrumentation:", err);
    }
  }

  private consumerId(requestInfo?: () => RequestInfo): string | null {
    const { consumerHeader, consumer, consumerSalt, hashConsumer } = this.config;
    if (!requestInfo || (!consumerHeader && !consumer)) return null;
    try {
      const info = requestInfo();
      const value = consumer ? consumer(info) : info.headers[consumerHeader!];
      if (value === undefined || value === null || value === "") return null;
      if (!hashConsumer) return String(value).slice(0, 128);
      const raw = String(value);
      let hashed = this.hashedConsumers.get(raw);
      if (hashed === undefined) {
        if (this.hashedConsumers.size >= 4096) this.hashedConsumers.clear(); // bounded
        hashed = createHmac("sha256", consumerSalt ?? "").update(raw).digest("hex").slice(0, 16);
        this.hashedConsumers.set(raw, hashed);
      }
      return hashed;
    } catch (err) {
      // A failing consumer function costs the consumer id, not the event.
      console.warn("reqly: consumer lookup failed:", err);
      return null;
    }
  }

  /** Sends everything queued. Safe to call any time; concurrent calls share one flush. */
  flush(): Promise<void> {
    if (this.flushing) return this.flushing;
    this.flushing = this.drain().finally(() => {
      this.flushing = undefined;
    });
    return this.flushing;
  }

  private async drain(): Promise<void> {
    // Only what was queued when the flush started. Looping until the queue
    // is empty would send each event that arrives during the sends as its
    // own one-event batch -- an HTTP call per request under steady traffic.
    let remaining = this.queue.length;
    while (remaining > 0 && !this.stopping) {
      const batch = this.queue.splice(0, Math.min(this.config.maxBatchSize, remaining));
      remaining -= batch.length;
      if (!(await this.send(batch))) {
        this.stats.failedBatches += 1;
        this.stats.dropped += batch.length;
      }
    }
  }

  private async send(events: WireEvent[], retries = MAX_RETRIES, timeoutMs = REQUEST_TIMEOUT_MS): Promise<boolean> {
    const body = JSON.stringify({
      service_name: this.config.serviceName,
      sdk_version: `node-${SDK_VERSION}`,
      ...(this.config.release ? { release: this.config.release } : {}),
      ...(this.config.environment ? { environment: this.config.environment } : {}),
      events,
    });
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.config.apiKey) headers["X-Reqly-Key"] = this.config.apiKey;
    for (let attempt = 0; attempt < retries; attempt++) {
      try {
        const response = await fetch(`${this.config.collectorUrl}/v1/ingest`, {
          method: "POST",
          headers,
          body,
          signal: AbortSignal.timeout(timeoutMs),
        });
        if (response.ok) {
          this.stats.shipped += events.length;
          return true;
        }
        if (!retryable(response.status)) {
          console.warn(`reqly: collector rejected batch with ${response.status}, dropping`);
          return false;
        }
      } catch {
        // network error or timeout: retry
      }
      // Not unref'd: while a flush is in flight (e.g. `await client.shutdown()`
      // with the collector down) the process must not exit mid-retry. The
      // waits are short (200 + 400 ms); the flush *interval* stays unref'd.
      // Jitter, so many processes don't retry against a restarting collector in lockstep.
      if (this.stopping) break; // shutdown() takes over, without retries
      if (attempt < retries - 1) {
        await new Promise((resolve) => setTimeout(resolve, 200 * 2 ** attempt + Math.random() * 100));
      }
    }
    return false;
  }

  /** Uploads the app's OpenAPI spec once. Never throws; failures are logged. */
  private async pushOpenapi(): Promise<void> {
    try {
      if (!this.openapiSource) {
        console.warn(
          "reqly: pushOpenapi needs a spec: pass the document or a function returning it " +
            "(Fastify with @fastify/swagger is picked up by itself)",
        );
        return;
      }
      const spec = await this.openapiSource();
      if (!spec || typeof spec !== "object") return;
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (this.config.apiKey) headers["X-Reqly-Key"] = this.config.apiKey;
      const url = `${this.config.collectorUrl}/v1/services/${encodeURIComponent(this.config.serviceName)}/openapi`;
      const response = await fetch(url, {
        method: "PUT",
        headers,
        body: JSON.stringify(spec),
        signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok) console.warn(`reqly: OpenAPI spec upload failed with ${response.status}`);
    } catch (err) {
      console.warn("reqly: OpenAPI spec upload failed:", err);
    }
  }

  /** Stops the timer and sends what is queued. Call on graceful shutdown. */
  async shutdown(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = undefined;
    liveClients.delete(this);
    // Bounded: one attempt per batch, no backoff, stop at the first failure
    // or after SHUTDOWN_FLUSH_MS. What is left counts as dropped.
    const deadline = Date.now() + SHUTDOWN_FLUSH_MS;
    this.stopping = true;
    await this.flushing; // a flush in flight ends after its current request
    while (this.queue.length > 0 && Date.now() < deadline) {
      const batch = this.queue.splice(0, this.config.maxBatchSize);
      const sent = await this.send(batch, 1, Math.max(1, Math.min(REQUEST_TIMEOUT_MS, deadline - Date.now())));
      if (!sent) {
        this.stats.failedBatches += 1;
        this.stats.dropped += batch.length;
        break;
      }
    }
    if (this.queue.length > 0) {
      console.warn(`reqly: dropped ${this.queue.length} unsent events at shutdown`);
      this.stats.dropped += this.queue.length;
      this.queue.length = 0;
    }
  }
}
