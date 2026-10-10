export const COLLECTOR_URL = import.meta.env.VITE_COLLECTOR_URL ?? "http://localhost:8000";
const READ_KEY = import.meta.env.VITE_READ_KEY ?? "demo-read-key";

if (!import.meta.env.VITE_READ_KEY) {
  console.warn(
    "[reqly] VITE_READ_KEY is not set — using the default 'demo-read-key'. " +
    "Set it in .env before deploying."
  );
}

const AUTH_HEADERS = { "X-Reqly-Key": READ_KEY };

export type TimeWindow = "1h" | "6h" | "24h" | "7d";

export interface LatencyPoint {
  bucket: string;
  request_count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
}

export interface ErrorRatePoint {
  bucket: string;
  request_count: number;
  error_count: number;
  error_rate: number;
}

export interface StatusDistributionPoint {
  status_code: number;
  count: number;
}

export interface TopRoute {
  route: string;
  request_count: number;
  p95_ms: number | null;
  error_rate: number | null;
}

export interface ReleaseMarker {
  release: string;
  first_seen_at: string;
}

export interface Release {
  release: string;
  first_seen_at: string;
  last_seen_at: string;
  environments: string[];
  request_count: number;
  error_rate: number | null;
  p95_ms: number | null;
}

export interface MetricsSummary {
  service_name: string;
  route: string | null;
  window: TimeWindow;
  latency: LatencyPoint[];
  error_rate: ErrorRatePoint[];
  status_distribution: StatusDistributionPoint[];
  top_routes: TopRoute[];
  request_rate: { requests_per_minute: number };
  releases?: ReleaseMarker[]; // collector >= 0.3
}

export interface Anomaly {
  route: string;
  day_of_week: string;
  hour_range: string;
  observed_error_rate: number;
  baseline_error_rate: number;
  observed_p95_ms: number;
  baseline_p95_ms: number;
  z_score: number;
  window_start?: string;
  release_context?: ReleaseContext | null;
  hints?: Hint[];
  affected_consumers?: AffectedConsumers;
}

export interface AffectedConsumers {
  active: number;
  affected: number;
  top: { consumer_id: string; requests: number; errors: number }[];
}

export interface Hint {
  dimension: string;
  value: string;
  text: string;
}

export interface SloStatus {
  state: "ok" | "slow_burn" | "fast_burn" | "budget_exhausted" | "no_data";
  sli: number | null;
  total: number;
  bad: number;
  budget_remaining: number | null;
  burn_rates: Record<"5m" | "30m" | "1h" | "6h", number>;
  window_days_covered: number;
}

export interface Slo {
  id: number;
  service_name: string;
  name: string;
  route: string | null;
  objective: "availability" | "latency";
  target: number;
  latency_threshold_ms: number | null;
  window_days: number;
  status: SloStatus;
}

export interface SloAlertDetails {
  kind: "slo";
  slo: Omit<Slo, "status">;
  status: SloStatus;
}

export interface Alert {
  id: number;
  kind?: "anomaly" | "slo";
  service_name: string;
  route: string;
  opened_at: string;
  first_hour: string;
  last_hour: string;
  resolved_at: string | null;
  details: Anomaly | SloAlertDetails;
}

export interface ReleaseStats {
  requests: number;
  error_rate: number | null;
  p95_ms: number | null;
}

export interface ReleaseContext {
  release: string;
  release_first_seen_at: string | null;
  is_new_release: boolean;
  previous_release?: string;
  before?: ReleaseStats;
  after?: ReleaseStats;
}

export interface InsightReport {
  service_name: string;
  week_start: string;
  anomalies_json: Anomaly[];
  report_text: string;
  generated_at?: string;
}

export interface DriftOperation {
  method: string;
  path: string;
  operation_id?: string;
  summary?: string;
  deprecated?: boolean;
}

export interface UndocumentedRoute {
  method: string;
  route: string;
  requests: number;
  error_rate: number | null;
  last_seen: string;
}

export interface DriftReport {
  service_name: string;
  spec: { title: string | null; version: string | null; base_path: string; uploaded_at: string };
  window_days: number;
  operations: number;
  documented_in_use: number;
  coverage: number | null;
  total_requests: number;
  undocumented_requests: number;
  unmatched_requests: number;
  undocumented: UndocumentedRoute[];
  dead: DriftOperation[];
  deprecated_in_use: (DriftOperation & {
    requests: number;
    last_seen: string;
    routes: string[];
    consumers?: { consumer_id: string; requests: number; last_seen: string }[];
  })[];
}

export type UsageWindow = "24h" | "7d" | "30d";

export interface ConsumerRow {
  consumer_id: string;
  requests: number;
  share_of_requests: number | null;
  errors: number;
  error_rate: number | null;
  routes: number;
  p95_ms: number | null;
  last_seen: string;
}

export interface ConsumersReport {
  window: UsageWindow;
  requests: number;
  requests_with_consumer: number;
  consumers: number;
  top: ConsumerRow[];
}

export interface LlmModelUsage {
  model: string;
  priced_as: string | null;
  llm_requests: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
}

export interface LlmRouteUsage {
  route: string;
  requests: number;
  llm_requests: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  cost_per_1k_requests: number;
  tokens_per_llm_request: number;
  models: LlmModelUsage[];
}

export interface LlmUsageReport {
  window: UsageWindow;
  totals: { requests: number; llm_requests: number; input_tokens: number; output_tokens: number; cost_usd: number };
  routes: LlmRouteUsage[];
  daily: { day: string; cost_usd: number }[];
  unpriced_models: { model: string; tokens: number }[];
  prices_as_of: string | null;
}

export interface AskStep {
  tool: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
}

export interface AskAnswer {
  answer: string;
  steps: AskStep[];
  model: string;
  unverified_numbers?: string[]; // numbers in the answer not found in any tool result
}

export class HttpError extends Error {
  constructor(
    message: string,
    public readonly status: number
  ) {
    super(message);
  }
}

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${COLLECTOR_URL}${path}`, {
    headers: AUTH_HEADERS,
  });
  if (!response.ok) {
    throw new HttpError(`GET ${path} failed: ${response.status}`, response.status);
  }
  return response.json();
}

async function postJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${COLLECTOR_URL}${path}`, {
    method: "POST",
    headers: AUTH_HEADERS,
  });
  if (!response.ok) {
    throw new HttpError(`POST ${path} failed: ${response.status}`, response.status);
  }
  return response.json();
}

async function sendJSON<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${COLLECTOR_URL}${path}`, {
    method: "POST",
    headers: { ...AUTH_HEADERS, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.json()).detail ?? "";
    } catch {
      // not JSON
    }
    throw new HttpError(typeof detail === "string" && detail ? detail : `POST ${path} failed: ${response.status}`, response.status);
  }
  return response.json();
}

export const api = {
  listServices: () => getJSON<{ services: string[] }>("/v1/services"),

  listRoutes: (serviceName: string) =>
    getJSON<{ routes: string[] }>(
      `/v1/services/${encodeURIComponent(serviceName)}/routes`
    ),

  getMetricsSummary: (serviceName: string, route: string | null, window: TimeWindow) => {
    const params = new URLSearchParams({ service_name: serviceName, window });
    if (route) params.set("route", route);
    return getJSON<MetricsSummary>(`/v1/metrics/summary?${params.toString()}`);
  },

  listReleases: (serviceName: string) =>
    getJSON<{ releases: Release[] }>(
      `/v1/services/${encodeURIComponent(serviceName)}/releases`
    ),

  listSlos: (serviceName: string) =>
    getJSON<{ slos: Slo[] }>(`/v1/slos?service_name=${encodeURIComponent(serviceName)}`),

  getConsumers: (serviceName: string, window: UsageWindow) =>
    getJSON<ConsumersReport>(`/v1/services/${encodeURIComponent(serviceName)}/consumers?window=${window}`),

  getLlmUsage: (serviceName: string, window: UsageWindow) =>
    getJSON<LlmUsageReport>(`/v1/services/${encodeURIComponent(serviceName)}/llm-usage?window=${window}`),

  getApiDrift: (serviceName: string) =>
    getJSON<DriftReport>(`/v1/services/${encodeURIComponent(serviceName)}/openapi/drift`),

  listAlerts: (serviceName: string) =>
    getJSON<{ alerts: Alert[] }>(`/v1/alerts?service_name=${encodeURIComponent(serviceName)}`),

  getLatestInsight: (serviceName: string) =>
    getJSON<InsightReport>(
      `/v1/insights/latest?service_name=${encodeURIComponent(serviceName)}`
    ),

  ask: (serviceName: string, question: string) =>
    sendJSON<AskAnswer>("/v1/ask", { service_name: serviceName, question }),

  generateInsight: (serviceName: string) =>
    postJSON<InsightReport>(
      `/v1/insights/generate?service_name=${encodeURIComponent(serviceName)}`
    ),
};
