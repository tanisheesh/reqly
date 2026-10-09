import { useReleases } from "../hooks/useMetrics";
import { Card } from "./Card";
import { formatMs, formatPercent } from "../format";

const ERROR_THRESHOLD_RAW = 0.05; // keep in sync with TopRoutesTable / ErrorRateChart

export function ReleasesTable({ serviceName }: { serviceName: string }) {
  const { data, isLoading, isError } = useReleases(serviceName);
  const releases = data?.releases ?? [];

  return (
    <Card title="Releases">
      {isLoading && <div className="h-16 animate-pulse rounded bg-slate-800/60" />}

      {isError && (
        <p className="text-xs text-slate-500">
          Release data needs collector 0.3 or later.
        </p>
      )}

      {!isLoading && !isError && releases.length === 0 && (
        <p className="py-6 text-center text-xs leading-relaxed text-slate-600">
          No releases recorded yet. Set <code className="font-mono text-slate-400">release=</code> in{" "}
          <code className="font-mono text-slate-400">reqly.instrument()</code> (or{" "}
          <code className="font-mono text-slate-400">REQLY_RELEASE</code>; common CI variables like{" "}
          <code className="font-mono text-slate-400">GITHUB_SHA</code> are picked up automatically), or{" "}
          <code className="font-mono text-slate-400">service.version</code> for OpenTelemetry apps.
        </p>
      )}

      {releases.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left text-slate-500">
                <th className="pb-2 pr-4 font-semibold">Release</th>
                <th className="pb-2 pr-4 font-semibold">First seen</th>
                <th className="pb-2 pr-4 text-right font-semibold">Requests (14d)</th>
                <th className="pb-2 pr-4 text-right font-semibold">p95</th>
                <th className="pb-2 text-right font-semibold">Error rate</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {releases.map((r) => (
                <tr key={r.release} className="text-slate-400">
                  <td className="py-2.5 pr-4">
                    <span className="font-mono text-violet-300">{r.release}</span>
                    {r.environments.length > 0 && (
                      <span className="ml-2 text-[11px] text-slate-600">{r.environments.join(", ")}</span>
                    )}
                  </td>
                  <td className="py-2.5 pr-4">{new Date(r.first_seen_at).toLocaleString()}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{r.request_count.toLocaleString()}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{formatMs(r.p95_ms)}</td>
                  <td
                    className={`py-2.5 text-right tabular-nums font-medium ${
                      (r.error_rate ?? 0) > ERROR_THRESHOLD_RAW ? "text-red-400" : "text-emerald-400"
                    }`}
                  >
                    {formatPercent(r.error_rate)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
