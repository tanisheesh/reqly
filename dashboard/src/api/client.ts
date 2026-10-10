import { clearSession, SessionUser, sessionToken } from "./auth";

// Settings written at container start (public/config.js) win over the ones
// baked in at build time, so one image works for any collector.
const runtimeConfig: { collectorUrl?: string; readKey?: string } =
  (window as unknown as { __REQLY_CONFIG__?: { collectorUrl?: string; readKey?: string } }).__REQLY_CONFIG__ ?? {};

export const COLLECTOR_URL =
  runtimeConfig.collectorUrl || import.meta.env.VITE_COLLECTOR_URL || "http://localhost:8000";
const READ_KEY = runtimeConfig.readKey || import.meta.env.VITE_READ_KEY || "demo-read-key";

if (!runtimeConfig.readKey && !import.meta.env.VITE_READ_KEY) {
  console.warn(
    "[reqly] No read key configured — using the default 'demo-read-key'. " +
    "Set REQLY_READ_KEY (Docker image) or VITE_READ_KEY (build) before deploying."
  );
}

// A signed-in user's session wins; otherwise the read key (accepted while
// the collector's PUBLIC_DASHBOARD is on).
function authHeaders(): Record<string, string> {
  const token = sessionToken();
  return token ? { Authorization: `Bearer ${token}` } : { "X-Reqly-Key": READ_KEY };
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const usedSession = sessionToken() !== null;
  const response = await fetch(`${COLLECTOR_URL}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers as Record<string, string> | undefined) },
  });
  // An expired or revoked session: drop it and go back to the sign-in page.
  if (response.status === 401 && usedSession) clearSession();
  return response;
}

async function errorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const detail = (await response.json()).detail;
    return typeof detail === "string" && detail ? detail : fallback;
  } catch {
    return fallback;
  }
}

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
  const response = await request(path);
  if (!response.ok) {
    throw new HttpError(`GET ${path} failed: ${response.status}`, response.status);
  }
  return response.json();
}

async function postJSON<T>(path: string): Promise<T> {
  const response = await request(path, { method: "POST" });
  if (!response.ok) {
    throw new HttpError(`POST ${path} failed: ${response.status}`, response.status);
  }
  return response.json();
}

async function sendJSON<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new HttpError(await errorDetail(response, `POST ${path} failed: ${response.status}`), response.status);
  }
  return response.json();
}

// Any method, JSON body, the collector's `detail` as the error message.
async function callJSON<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await request(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new HttpError(await errorDetail(response, `${method} ${path} failed: ${response.status}`), response.status);
  }
  return response.json();
}

export interface Project {
  id: number;
  slug: string;
  name: string;
  created_at: string;
  services: string[];
}

export type KeyScope = "ingest" | "read" | "admin";

export interface ApiKey {
  id: number;
  project_id: number;
  name: string;
  prefix: string;
  scopes: KeyScope[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface Member {
  user_id: number;
  username: string;
  is_admin: boolean;
  added_at: string;
}

export const projectsApi = {
  list: () => getJSON<{ projects: Project[] }>("/v1/projects"),
  create: (slug: string, name: string) => callJSON<Project>("POST", "/v1/projects", { slug, name }),
  moveService: (projectId: number, serviceName: string) =>
    callJSON<{ service_name: string }>("PUT", `/v1/projects/${projectId}/services`, { service_name: serviceName }),
  keys: (projectId: number) => getJSON<{ keys: ApiKey[] }>(`/v1/projects/${projectId}/keys`),
  createKey: (projectId: number, name: string, scopes: KeyScope[]) =>
    callJSON<ApiKey & { key: string }>("POST", `/v1/projects/${projectId}/keys`, { name, scopes }),
  revokeKey: (keyId: number) => callJSON<{ revoked: number }>("POST", `/v1/keys/${keyId}/revoke`),
  members: (projectId: number) => getJSON<{ members: Member[] }>(`/v1/projects/${projectId}/members`),
  addMember: (projectId: number, username: string) =>
    callJSON<Member>("POST", `/v1/projects/${projectId}/members`, { username }),
  removeMember: (projectId: number, userId: number) =>
    callJSON<{ removed: number }>("DELETE", `/v1/projects/${projectId}/members/${userId}`),
};

export const accountApi = {
  changePassword: (currentPassword: string, newPassword: string) =>
    callJSON<{ changed: boolean }>("POST", "/v1/auth/password", {
      current_password: currentPassword,
      new_password: newPassword,
    }),
};

export interface AuthConfig {
  public_dashboard: boolean;
  login: boolean;
}

export interface LoginResult {
  token: string;
  expires_at: string;
  user: SessionUser;
}

export const authApi = {
  /** Collectors before 0.9 have no auth endpoints: treat them as public, no login. */
  config: async (): Promise<AuthConfig> => {
    try {
      const response = await fetch(`${COLLECTOR_URL}/v1/auth/config`);
      if (!response.ok) return { public_dashboard: true, login: false };
      return response.json();
    } catch {
      return { public_dashboard: true, login: false };
    }
  },

  login: async (username: string, password: string): Promise<LoginResult> => {
    const response = await fetch(`${COLLECTOR_URL}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      const fallback = response.status === 429 ? "Too many attempts; wait a minute." : "Sign-in failed.";
      throw new HttpError(await errorDetail(response, fallback), response.status);
    }
    return response.json();
  },

  logout: async (): Promise<void> => {
    const token = sessionToken();
    if (token) {
      try {
        await fetch(`${COLLECTOR_URL}/v1/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      } catch {
        // signing out locally is what matters
      }
    }
  },
};

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
