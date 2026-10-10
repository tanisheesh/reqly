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

  listAlerts: (serviceName: string) =>
    getJSON<{ alerts: Alert[] }>(`/v1/alerts?service_name=${encodeURIComponent(serviceName)}`),

  getLatestInsight: (serviceName: string) =>
    getJSON<InsightReport>(
      `/v1/insights/latest?service_name=${encodeURIComponent(serviceName)}`
    ),

  generateInsight: (serviceName: string) =>
    postJSON<InsightReport>(
      `/v1/insights/generate?service_name=${encodeURIComponent(serviceName)}`
    ),
};
