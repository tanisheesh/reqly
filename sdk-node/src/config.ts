import type { RequestInfo } from "./context.js";

export interface ReqlyOptions {
  /** Service name shown in Reqly. Default: REQLY_SERVICE_NAME, else npm_package_name, else "unnamed-service". */
  serviceName?: string;
  /** Collector base URL. Default: REQLY_COLLECTOR_URL or http://localhost:8000. */
  collectorUrl?: string;
  /** Ingest key (X-Reqly-Key). Default: REQLY_API_KEY. */
  apiKey?: string;
  /** Release running now. Default: REQLY_RELEASE, then CI variables (GITHUB_SHA, RENDER_GIT_COMMIT, ...). */
  release?: string;
  /** Deployment environment, e.g. "production". Default: REQLY_ENVIRONMENT. */
  environment?: string;
  /** Share of requests recorded, 0..1. Default: REQLY_SAMPLE_RATE or 1. */
  sampleRate?: number;
  /** How often batches are sent. Default 5000 ms. */
  flushIntervalMs?: number;
  /** Events per request to the collector. Default 200. */
  maxBatchSize?: number;
  /** Events kept while the collector is unreachable; older ones are dropped. Default 2000. */
  maxQueueSize?: number;
  /** Route templates not recorded. Default ["/health", "/metrics"]. */
  ignoreRoutes?: string[];
  /** Header naming the caller, e.g. "x-api-key". Default: REQLY_CONSUMER_HEADER. */
  consumerHeader?: string;
  /** Caller id from the request, instead of a header. */
  consumer?: (info: RequestInfo) => string | null | undefined;
  /** Secret for hashing consumer ids (HMAC-SHA256). Default: REQLY_CONSUMER_SALT. */
  consumerSalt?: string;
  /** Hash consumer ids before they leave the app. Default true (REQLY_HASH_CONSUMER). */
  hashConsumer?: boolean;
  /**
   * Upload the app's OpenAPI spec once, on the first request, for API drift:
   * the spec object (e.g. NestJS `SwaggerModule.createDocument(...)`), a
   * function returning it (sync or async), or `true` to take it from the
   * framework (Fastify with @fastify/swagger). Default: REQLY_PUSH_OPENAPI.
   */
  pushOpenapi?: boolean | object | (() => unknown);
}

export interface ResolvedConfig {
  serviceName: string;
  collectorUrl: string;
  apiKey?: string;
  release?: string;
  environment?: string;
  sampleRate: number;
  flushIntervalMs: number;
  maxBatchSize: number;
  maxQueueSize: number;
  ignoreRoutes: Set<string>;
  consumerHeader?: string;
  consumer?: (info: RequestInfo) => string | null | undefined;
  consumerSalt?: string;
  hashConsumer: boolean;
  pushOpenapi: boolean | object | (() => unknown);
}

// Commit SHA variables set by common CI/CD and hosting platforms (same list
// as the Python SDK).
const RELEASE_ENV_VARS = [
  "REQLY_RELEASE",
  "GIT_COMMIT",
  "GITHUB_SHA",
  "CI_COMMIT_SHA",
  "RENDER_GIT_COMMIT",
  "VERCEL_GIT_COMMIT_SHA",
  "RAILWAY_GIT_COMMIT_SHA",
  "HEROKU_SLUG_COMMIT",
  "SOURCE_VERSION",
  "K_REVISION",
];

function env(name: string): string | undefined {
  const value = process.env[name];
  return value === undefined || value.trim() === "" ? undefined : value.trim();
}

function envNumber(name: string): number | undefined {
  const value = env(name);
  const parsed = value === undefined ? NaN : Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function envBool(name: string): boolean | undefined {
  const value = env(name);
  return value === undefined ? undefined : ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

/** The collector's per-request limit. */
const MAX_BATCH_SIZE = 1000;
const MIN_FLUSH_INTERVAL_MS = 100;

// Out-of-range values would quietly break shipping: a batch above the
// collector's limit is rejected whole, a batch size below 1 makes the flush
// loop forever, and a 0 ms interval is a busy timer.
function clampInt(value: number, min: number, max = Infinity): number {
  return Math.min(Math.max(Math.floor(value), min), max);
}

export function resolveConfig(options: ReqlyOptions = {}): ResolvedConfig {
  const release = options.release ?? RELEASE_ENV_VARS.map(env).find((v) => v !== undefined);
  const sampleRate = options.sampleRate ?? envNumber("REQLY_SAMPLE_RATE") ?? 1;
  return {
    serviceName: options.serviceName ?? env("REQLY_SERVICE_NAME") ?? env("npm_package_name") ?? "unnamed-service",
    collectorUrl: (options.collectorUrl ?? env("REQLY_COLLECTOR_URL") ?? "http://localhost:8000").replace(/\/+$/, ""),
    apiKey: options.apiKey ?? env("REQLY_API_KEY"),
    release: release?.slice(0, 128),
    environment: (options.environment ?? env("REQLY_ENVIRONMENT"))?.slice(0, 32),
    sampleRate: Math.min(1, Math.max(0, sampleRate)),
    // REQLY_FLUSH_INTERVAL_SECONDS is the Python SDK's name; accepted too, so
    // one environment configures both SDKs.
    flushIntervalMs: Math.max(
      options.flushIntervalMs ??
        envNumber("REQLY_FLUSH_INTERVAL_MS") ??
        (envNumber("REQLY_FLUSH_INTERVAL_SECONDS") !== undefined ? envNumber("REQLY_FLUSH_INTERVAL_SECONDS")! * 1000 : 5000),
      MIN_FLUSH_INTERVAL_MS,
    ),
    maxBatchSize: clampInt(options.maxBatchSize ?? envNumber("REQLY_MAX_BATCH_SIZE") ?? 200, 1, MAX_BATCH_SIZE),
    maxQueueSize: clampInt(options.maxQueueSize ?? envNumber("REQLY_MAX_QUEUE_SIZE") ?? 2000, 1),
    ignoreRoutes: new Set(
      options.ignoreRoutes ??
        (env("REQLY_IGNORE_ROUTES") ?? "/health,/metrics").split(",").map((r) => r.trim()).filter(Boolean),
    ),
    consumerHeader: (options.consumerHeader ?? env("REQLY_CONSUMER_HEADER"))?.toLowerCase(),
    consumer: options.consumer,
    consumerSalt: options.consumerSalt ?? env("REQLY_CONSUMER_SALT"),
    hashConsumer: options.hashConsumer ?? envBool("REQLY_HASH_CONSUMER") ?? true,
    pushOpenapi: options.pushOpenapi ?? envBool("REQLY_PUSH_OPENAPI") ?? false,
  };
}
