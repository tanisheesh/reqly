import { createHmac, randomUUID } from "node:crypto";
import { hostname } from "node:os";

import { ReqlyOptions, ResolvedConfig, resolveConfig } from "./config.js";
import { LlmSummary, RequestInfo } from "./context.js";

export const SDK_VERSION = "0.2.0";
export const UNMATCHED_ROUTE = "__unmatched__";

const HOST = hostname();
const MAX_RETRIES = 3;
const REQUEST_TIMEOUT_MS = 3000;

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
  process.on("beforeExit", () => {
    for (const client of liveClients) {
      if (client.hasQueued()) void client.flush();
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
  // The same few API keys / tenants call over and over: hash each once.
  private readonly hashedConsumers = new Map<string, string>();
  readonly stats = { recorded: 0, shipped: 0, dropped: 0, failedBatches: 0 };

  constructor(options: ReqlyOptions = {}) {
    this.config = resolveConfig(options);
    if (this.config.hashConsumer && (this.config.consumerHeader || this.config.consumer) && !this.config.consumerSalt) {
      console.warn(
        "reqly: consumer tracking without REQLY_CONSUMER_SALT -- hashed ids of guessable values " +
          "(user ids, emails) can be reversed by trying candidates; set a secret salt",
      );
    }
    this.timer = setInterval(() => void this.flush(), this.config.flushIntervalMs);
    this.timer.unref(); // never keeps the process alive
    liveClients.add(this);
    installExitHook();
  }

  /** True while events are waiting to be sent. */
  hasQueued(): boolean {
    return this.queue.length > 0;
  }

  record(request: RecordedRequest): void {
    if (this.disabled) return;
    try {
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
    while (remaining > 0) {
      const batch = this.queue.splice(0, Math.min(this.config.maxBatchSize, remaining));
      remaining -= batch.length;
      if (!(await this.send(batch))) {
        this.stats.failedBatches += 1;
        this.stats.dropped += batch.length;
      }
    }
  }

  private async send(events: WireEvent[]): Promise<boolean> {
    const body = JSON.stringify({
      service_name: this.config.serviceName,
      sdk_version: `node-${SDK_VERSION}`,
      ...(this.config.release ? { release: this.config.release } : {}),
      ...(this.config.environment ? { environment: this.config.environment } : {}),
      events,
    });
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.config.apiKey) headers["X-Reqly-Key"] = this.config.apiKey;
    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      try {
        const response = await fetch(`${this.config.collectorUrl}/v1/ingest`, {
          method: "POST",
          headers,
          body,
          signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
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
      if (attempt < MAX_RETRIES - 1) await new Promise((resolve) => setTimeout(resolve, 200 * 2 ** attempt));
    }
    return false;
  }

  /** Stops the timer and sends what is queued. Call on graceful shutdown. */
  async shutdown(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = undefined;
    liveClients.delete(this);
    // A flush sends what was queued when it started; keep going until the
    // queue is empty (failed batches are dropped, so this ends).
    do {
      await this.flush();
    } while (this.queue.length > 0);
  }
}
