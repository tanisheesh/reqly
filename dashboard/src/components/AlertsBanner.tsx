import { Anomaly, SloAlertDetails } from "../api/client";
import { useAlerts } from "../hooks/useMetrics";
import { formatMs, formatPercent } from "../format";

export function AlertsBanner({ serviceName }: { serviceName: string }) {
  const { data } = useAlerts(serviceName);
  const alerts = data?.alerts ?? [];
  if (alerts.length === 0) return null;

  return (
    <div className="rounded-xl border border-red-500/25 bg-red-500/[0.06] px-5 py-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="h-2 w-2 animate-pulse rounded-full bg-red-400" />
        <h3 className="text-[11px] font-semibold uppercase tracking-widest text-red-300">
          {alerts.length} open alert{alerts.length > 1 ? "s" : ""}
        </h3>
      </div>
      <ul className="space-y-3">
        {alerts.map((a) => {
          if (a.kind === "slo") {
            const s = a.details as SloAlertDetails;
            return (
              <li key={a.id} className="text-xs leading-relaxed">
                <span className="font-semibold text-slate-200">SLO {s.slo.name}</span>{" "}
                <span className="text-red-300">
                  {s.status.state === "fast_burn" ? "fast" : "slow"} burn — {s.status.burn_rates["1h"]}× (1h)
                </span>{" "}
                <span className="text-slate-500">
                  · {s.status.budget_remaining === null ? "—" : `${Math.max(0, s.status.budget_remaining * 100).toFixed(0)}%`} of budget left
                </span>
              </li>
            );
          }
          const d = a.details as Anomaly;
          const ctx = d.release_context;
          return (
            <li key={a.id} className="text-xs leading-relaxed">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-mono font-semibold text-slate-200">{a.route}</span>
                <span className="text-slate-400">
                  errors {formatPercent(d.observed_error_rate)} vs {formatPercent(d.baseline_error_rate)} usual · p95{" "}
                  {formatMs(d.observed_p95_ms)} vs {formatMs(d.baseline_p95_ms)} usual
                </span>
                <span className="text-slate-600">
                  since {new Date(a.first_hour).toLocaleString()} · last seen{" "}
                  {new Date(a.last_hour).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
              {ctx?.is_new_release && (
                <div className="text-violet-300">
                  running new release <span className="font-mono">{ctx.release}</span>
                  {ctx.previous_release && <> (previous {ctx.previous_release})</>}
                </div>
              )}
              {(d.hints ?? []).map((h) => (
                <div key={`${h.dimension}:${h.value}`} className="text-amber-300/90">
                  {h.text}
                </div>
              ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
