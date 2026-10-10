import { useQuery } from "@tanstack/react-query";
import { api, TimeWindow, UsageWindow } from "../api/client";

// Plain REST polling, not WebSocket/SSE: the underlying continuous
// aggregates refresh at 1-minute / 1-hour granularity server-side, so a
// 10-15s poll is already nearly as fresh as data can get -- a push
// transport would add real infra complexity for no real freshness gain.
const METRICS_POLL_MS = 15_000;
const LIST_POLL_MS = 30_000;

export function useServices() {
  return useQuery({
    queryKey: ["services"],
    queryFn: api.listServices,
    refetchInterval: LIST_POLL_MS,
  });
}

export function useRoutes(serviceName: string | null) {
  return useQuery({
    queryKey: ["routes", serviceName],
    queryFn: () => api.listRoutes(serviceName!),
    enabled: !!serviceName,
    refetchInterval: LIST_POLL_MS,
  });
}

export function useReleases(serviceName: string | null) {
  return useQuery({
    queryKey: ["releases", serviceName],
    queryFn: () => api.listReleases(serviceName!),
    enabled: !!serviceName,
    refetchInterval: LIST_POLL_MS,
  });
}

export function useSlos(serviceName: string | null) {
  return useQuery({
    queryKey: ["slos", serviceName],
    queryFn: () => api.listSlos(serviceName!),
    enabled: !!serviceName,
    refetchInterval: 60_000,
    retry: false, // collectors older than 0.5 have no /v1/slos
  });
}

export function useConsumers(serviceName: string | null, window: UsageWindow) {
  return useQuery({
    queryKey: ["consumers", serviceName, window],
    queryFn: () => api.getConsumers(serviceName!, window),
    enabled: !!serviceName,
    refetchInterval: 300_000,
    retry: false, // collectors before 0.8 have no endpoint
  });
}

export function useLlmUsage(serviceName: string | null, window: UsageWindow) {
  return useQuery({
    queryKey: ["llm-usage", serviceName, window],
    queryFn: () => api.getLlmUsage(serviceName!, window),
    enabled: !!serviceName,
    refetchInterval: 300_000,
    retry: false,
  });
}

export function useApiDrift(serviceName: string | null) {
  return useQuery({
    queryKey: ["api-drift", serviceName],
    queryFn: () => api.getApiDrift(serviceName!),
    enabled: !!serviceName,
    refetchInterval: 300_000,
    retry: false, // 404 = no spec uploaded; older collectors have no endpoint
  });
}

export function useAlerts(serviceName: string | null) {
  return useQuery({
    queryKey: ["alerts", serviceName],
    queryFn: () => api.listAlerts(serviceName!),
    enabled: !!serviceName,
    refetchInterval: 60_000, // alerts change at most hourly
    retry: false, // collectors older than 0.4 have no /v1/alerts
  });
}

export function useMetricsSummary(
  serviceName: string | null,
  route: string | null,
  window: TimeWindow
) {
  return useQuery({
    queryKey: ["metrics-summary", serviceName, route, window],
    queryFn: () => api.getMetricsSummary(serviceName!, route, window),
    enabled: !!serviceName,
    refetchInterval: METRICS_POLL_MS,
  });
}
