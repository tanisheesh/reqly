import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export function useLatestInsight(serviceName: string | null) {
  return useQuery({
    queryKey: ["insights-latest", serviceName],
    queryFn: () => api.getLatestInsight(serviceName!),
    enabled: !!serviceName,
    retry: false, // 404 (no report yet) is an expected, valid state
  });
}

export function useGenerateInsight(serviceName: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.generateInsight(serviceName!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["insights-latest", serviceName] });
    },
  });
}
