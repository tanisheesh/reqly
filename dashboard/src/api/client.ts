const COLLECTOR_URL = import.meta.env.VITE_COLLECTOR_URL ?? "http://localhost:8000";
const READ_KEY = import.meta.env.VITE_READ_KEY ?? "demo-key";

if (!import.meta.env.VITE_READ_KEY) {
  console.warn(
    "[reqly] VITE_READ_KEY is not set — using the default 'demo-key'. " +
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

export interface MetricsSummary {
  service_name: string;
  route: string | null;
  window: TimeWindow;
  latency: LatencyPoint[];
  error_rate: ErrorRatePoint[];
  status_distribution: StatusDistributionPoint[];
  top_routes: TopRoute[];
  request_rate: { requests_per_minute: number };
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
}

export interface InsightReport {
  service_name: string;
  week_start: string;
  anomalies_json: Anomaly[];
  report_text: string;
  generated_at?: string;
}

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${COLLECTOR_URL}${path}`, {
    headers: AUTH_HEADERS,
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed: ${response.status}`);
  }
  return response.json();
}

async function postJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${COLLECTOR_URL}${path}`, {
    method: "POST",
    headers: AUTH_HEADERS,
  });
  if (!response.ok) {
    throw new Error(`POST ${path} failed: ${response.status}`);
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

  getLatestInsight: (serviceName: string) =>
    getJSON<InsightReport>(
      `/v1/insights/latest?service_name=${encodeURIComponent(serviceName)}`
    ),

  generateInsight: (serviceName: string) =>
    postJSON<InsightReport>(
      `/v1/insights/generate?service_name=${encodeURIComponent(serviceName)}`
    ),
};
