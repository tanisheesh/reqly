/**
 * The incoming request's trace id, for trace correlation in alerts: W3C
 * `traceparent`, then Sentry's `sentry-trace`, then Zipkin's `x-b3-traceid`.
 * Only read for failed (4xx/5xx) or slow requests -- the collector keeps no
 * other -- so a fast 200 never pays for it.
 */
export const TRACE_SLOW_MS = 500; // default; the traceSlowMs option (the collector keeps 1s+: TRACE_ID_SLOW_MS)

const HEX = /^(?:[0-9a-f]{16}|[0-9a-f]{32})$/;

function valid(value: string | undefined): string | undefined {
  const id = value?.trim().toLowerCase();
  return id && HEX.test(id) && /[1-9a-f]/.test(id) ? id : undefined;
}

/** headers: lower-case names. */
export function traceIdFromHeaders(headers: Record<string, string | undefined>): string | undefined {
  const parts = headers.traceparent?.split("-"); // version-traceid-parentid-flags
  if (parts && parts.length >= 4 && parts[1].length === 32) {
    const id = valid(parts[1]);
    if (id) return id;
  }
  return valid(headers["sentry-trace"]?.split("-")[0]) ?? valid(headers["x-b3-traceid"]);
}

export function wantsTraceId(statusCode: number, durationMs: number, slowMs = TRACE_SLOW_MS): boolean {
  return statusCode >= 400 || durationMs >= slowMs;
}
