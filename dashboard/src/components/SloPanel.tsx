import { Slo } from "../api/client";
import { useSlos } from "../hooks/useMetrics";
import { Card } from "./Card";

const STATE_STYLE: Record<string, { label: string; cls: string }> = {
  ok: { label: "on track", cls: "bg-emerald-500/10 text-emerald-400 ring-emerald-500/20" },
  slow_burn: { label: "slow burn", cls: "bg-amber-500/10 text-amber-300 ring-amber-500/25" },
  fast_burn: { label: "fast burn", cls: "bg-red-500/10 text-red-400 ring-red-500/25" },
  budget_exhausted: { label: "budget spent", cls: "bg-red-500/10 text-red-400 ring-red-500/25" },
  no_data: { label: "no data", cls: "bg-slate-500/10 text-slate-400 ring-slate-500/20" },
};

function objectiveText(slo: Slo): string {
  const target = `${(slo.target * 100).toFixed(slo.target >= 0.999 ? 2 : 1)}%`;
  const scope = slo.route ?? "all routes";
  return slo.objective === "availability"
    ? `${target} without errors · ${scope} · ${slo.window_days}d`
    : `${target} under ${slo.latency_threshold_ms}ms · ${scope} · ${slo.window_days}d`;
}

function BudgetBar({ remaining }: { remaining: number | null }) {
  if (remaining === null) return <div className="h-1.5 rounded bg-slate-800" />;
  const pct = Math.max(0, Math.min(1, remaining)) * 100;
  const color = remaining > 0.5 ? "bg-emerald-500" : remaining > 0.2 ? "bg-amber-400" : "bg-red-500";
  return (
    <div className="h-1.5 overflow-hidden rounded bg-slate-800">
      <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function SloPanel({ serviceName }: { serviceName: string }) {
  const { data, isError } = useSlos(serviceName);
  const slos = data?.slos ?? [];
  if (isError || slos.length === 0) return null; // SLOs are opt-in; no empty card

  return (
    <Card title="SLOs & error budgets">
      <div className="grid gap-3 sm:grid-cols-2">
        {slos.map((slo) => {
          const st = slo.status;
          const style = STATE_STYLE[st.state] ?? STATE_STYLE.no_data;
          return (
            <div key={slo.id} className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium text-slate-200">{slo.name}</span>
                <span className={`shrink-0 rounded-md px-2 py-0.5 text-[11px] font-semibold ring-1 ${style.cls}`}>
                  {style.label}
                </span>
              </div>
              <p className="mb-3 text-[11px] text-slate-500">{objectiveText(slo)}</p>
              <div className="mb-1 flex items-baseline justify-between text-xs">
                <span className="text-slate-400">
                  SLI{" "}
                  <span className="font-mono text-slate-200">
                    {st.sli === null ? "—" : `${(st.sli * 100).toFixed(3)}%`}
                  </span>
                </span>
                <span className="text-slate-500">
                  budget left{" "}
                  <span className="font-mono text-slate-300">
                    {st.budget_remaining === null ? "—" : `${Math.max(0, st.budget_remaining * 100).toFixed(0)}%`}
                  </span>
                </span>
              </div>
              <BudgetBar remaining={st.budget_remaining} />
              <p className="mt-2 font-mono text-[11px] text-slate-500">
                burn 1h {st.burn_rates["1h"]}× · 6h {st.burn_rates["6h"]}×
              </p>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
