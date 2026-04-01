import { useState } from "react";
import { useGenerateInsight, useLatestInsight } from "../hooks/useInsights";
import { Card } from "./Card";
import { formatMs, formatPercent } from "../format";

export function InsightsPanel({ serviceName }: { serviceName: string }) {
  const { data, isLoading, isError } = useLatestInsight(serviceName);
  const generate = useGenerateInsight(serviceName);
  const [showData, setShowData] = useState(false);

  const action = (
    <button
      onClick={() => generate.mutate()}
      disabled={generate.isPending}
      className="flex items-center gap-1.5 rounded-md bg-cyan-600/15 px-3 py-1 text-xs font-semibold text-cyan-400 ring-1 ring-cyan-600/30 transition-colors hover:bg-cyan-600/25 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {generate.isPending ? (
        <>
          <span className="inline-block h-3 w-3 animate-spin rounded-full border border-current border-t-transparent" />
          Generating…
        </>
      ) : (
        "Regenerate"
      )}
    </button>
  );

  return (
    <Card title="AI Insights — weekly anomaly report" action={action}>
      {isLoading && (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-4 animate-pulse rounded bg-slate-800" style={{ width: `${85 - i * 10}%` }} />
          ))}
        </div>
      )}

      {isError && !isLoading && (
        <div className="rounded-lg border border-dashed border-slate-800 py-8 text-center">
          <p className="mb-1 text-sm text-slate-400">No report yet for this service.</p>
          <p className="text-xs text-slate-600">
            Click Regenerate, or wait for the weekly run (Sunday 23:00 UTC).
          </p>
        </div>
      )}

      {data && (
        <div className="space-y-4">
          <p className="text-xs text-slate-600">
            Week of {data.week_start}
            {data.generated_at && (
              <> · generated {new Date(data.generated_at).toLocaleString()}</>
            )}
          </p>

          <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
            {data.report_text}
          </div>

          {data.anomalies_json.length > 0 && (
            <div>
              <button
                onClick={() => setShowData((v) => !v)}
                className="text-xs font-medium text-cyan-500 hover:text-cyan-400 hover:underline"
              >
                {showData ? "Hide" : "Show"} anomaly data
              </button>

              {showData && (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[600px] text-xs">
                    <thead>
                      <tr className="border-b border-slate-800 text-left text-slate-500">
                        <th className="pb-2 pr-4 font-semibold">Route</th>
                        <th className="pb-2 pr-4 font-semibold">When</th>
                        <th className="pb-2 pr-4 text-right font-semibold">Err (obs)</th>
                        <th className="pb-2 pr-4 text-right font-semibold">Err (base)</th>
                        <th className="pb-2 pr-4 text-right font-semibold">p95 (obs)</th>
                        <th className="pb-2 pr-4 text-right font-semibold">p95 (base)</th>
                        <th className="pb-2 text-right font-semibold">z-score</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60">
                      {data.anomalies_json.map((a, i) => (
                        <tr key={i} className="text-slate-400">
                          <td className="py-2 pr-4 font-mono text-slate-300">{a.route}</td>
                          <td className="py-2 pr-4">
                            {a.day_of_week} {a.hour_range}
                          </td>
                          <td className="py-2 pr-4 text-right tabular-nums text-red-400">
                            {formatPercent(a.observed_error_rate)}
                          </td>
                          <td className="py-2 pr-4 text-right tabular-nums">
                            {formatPercent(a.baseline_error_rate)}
                          </td>
                          <td className="py-2 pr-4 text-right tabular-nums text-red-400">
                            {formatMs(a.observed_p95_ms)}
                          </td>
                          <td className="py-2 pr-4 text-right tabular-nums">
                            {formatMs(a.baseline_p95_ms)}
                          </td>
                          <td className="py-2 text-right tabular-nums font-medium text-amber-400">
                            {a.z_score.toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
