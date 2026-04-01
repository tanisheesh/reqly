import { useQuery } from "@tanstack/react-query";
import { api, TimeWindow } from "../api/client";

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
