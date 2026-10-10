import { useState } from "react";
import { DriftReport } from "../api/client";
import { formatPercent } from "../format";
import { useApiDrift } from "../hooks/useMetrics";
import { Card } from "./Card";

const PREVIEW_ROWS = 6;
type Tab = "undocumented" | "deprecated" | "dead";

function Method({ method }: { method: string }) {
  return <span className="inline-block w-14 font-mono text-[11px] font-semibold text-cyan-400">{method}</span>;
}

function Stat({ label, value, tone = "slate" }: { label: string; value: string; tone?: "slate" | "amber" | "red" }) {
  const color = { slate: "text-slate-200", amber: "text-amber-300", red: "text-red-400" }[tone];
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">{label}</p>
      <p className={`font-mono text-lg font-semibold ${color}`}>{value}</p>
    </div>
  );
}

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

function Rows({ report, tab, all }: { report: DriftReport; tab: Tab; all: boolean }) {
  const limit = (n: number) => (all ? n : Math.min(n, PREVIEW_ROWS));

  if (tab === "undocumented") {
    if (report.undocumented.length === 0) return <Empty text="Every endpoint that gets traffic is in the spec." />;
    return (
      <ul className="divide-y divide-slate-800/60">
        {report.undocumented.slice(0, limit(report.undocumented.length)).map((u) => (
          <li key={`${u.method} ${u.route}`} className="flex items-center gap-2 py-2 text-xs">
            <Method method={u.method} />
            <span className="min-w-0 flex-1 truncate font-mono text-slate-300">{u.route}</span>
            <span className="tabular-nums text-slate-400">{u.requests.toLocaleString()} req</span>
            <span className="hidden w-16 text-right tabular-nums text-slate-500 sm:block">{formatPercent(u.error_rate)} err</span>
            <span className="hidden w-14 text-right text-slate-600 sm:block">{shortDate(u.last_seen)}</span>
          </li>
        ))}
      </ul>
    );
  }

  if (tab === "deprecated") {
    if (report.deprecated_in_use.length === 0) return <Empty text="No deprecated endpoint is still being called." />;
    return (
      <ul className="divide-y divide-slate-800/60">
        {report.deprecated_in_use.slice(0, limit(report.deprecated_in_use.length)).map((d) => (
          <li key={`${d.method} ${d.path}`} className="flex items-center gap-2 py-2 text-xs">
            <Method method={d.method} />
            <span className="min-w-0 flex-1 truncate font-mono text-slate-300" title={d.summary}>{d.path}</span>
            {(d.consumers ?? []).length > 0 && (
              <span className="hidden truncate font-mono text-[11px] text-slate-500 md:block" title="consumers still calling it">
                {d.consumers!.slice(0, 3).map((c) => c.consumer_id).join(", ")}
                {d.consumers!.length > 3 && ` +${d.consumers!.length - 3}`}
              </span>
            )}
            <span className="tabular-nums text-amber-300">{d.requests.toLocaleString()} req</span>
            <span className="hidden w-24 text-right text-slate-600 sm:block">last {shortDate(d.last_seen)}</span>
          </li>
        ))}
      </ul>
    );
  }

  if (report.dead.length === 0) return <Empty text={`Every operation in the spec was called in the last ${report.window_days} days.`} />;
  return (
    <ul className="divide-y divide-slate-800/60">
      {report.dead.slice(0, limit(report.dead.length)).map((d) => (
        <li key={`${d.method} ${d.path}`} className="flex items-center gap-2 py-2 text-xs">
          <Method method={d.method} />
          <span className="min-w-0 flex-1 truncate font-mono text-slate-400">{d.path}</span>
          {d.operation_id && <span className="hidden truncate font-mono text-[11px] text-slate-600 sm:block">{d.operation_id}</span>}
          {d.deprecated && <span className="text-[10px] text-slate-500">deprecated</span>}
        </li>
      ))}
    </ul>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="py-4 text-center text-xs text-slate-600">{text}</p>;
}

export function ApiSurfacePanel({ serviceName }: { serviceName: string }) {
  const { data: report, isError } = useApiDrift(serviceName);
  const [tab, setTab] = useState<Tab>("undocumented");
  const [all, setAll] = useState(false);
  if (isError || !report) return null; // opt-in: hidden until a spec is uploaded

  const counts: Record<Tab, number> = {
    undocumented: report.undocumented.length,
    deprecated: report.deprecated_in_use.length,
    dead: report.dead.length,
  };
  const labels: Record<Tab, string> = {
    undocumented: "Undocumented",
    deprecated: "Deprecated, still used",
    dead: `Unused (${report.window_days}d)`,
  };
  const undocShare = report.total_requests ? report.undocumented_requests / report.total_requests : null;
  const specName = [report.spec.title, report.spec.version && `v${report.spec.version}`].filter(Boolean).join(" ");

  return (
    <Card
      title="API surface vs OpenAPI spec"
      action={
        <span className="text-[11px] text-slate-600">
          {specName || "spec"} · uploaded {shortDate(report.spec.uploaded_at)}
        </span>
      }
    >
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat
          label="Spec coverage"
          value={`${report.documented_in_use}/${report.operations}`}
        />
        <Stat label="Undocumented traffic" value={formatPercent(undocShare)} tone={counts.undocumented ? "amber" : "slate"} />
        <Stat label="Deprecated in use" value={String(counts.deprecated)} tone={counts.deprecated ? "amber" : "slate"} />
        <Stat label="404s (no route)" value={report.unmatched_requests.toLocaleString()} />
      </div>

      <div className="mb-2 flex flex-wrap gap-1.5">
        {(Object.keys(labels) as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => { setTab(t); setAll(false); }}
            className={`rounded-md px-2.5 py-1 text-[11px] font-semibold ring-1 transition-colors ${
              tab === t
                ? "bg-cyan-600/15 text-cyan-300 ring-cyan-600/30"
                : "text-slate-500 ring-slate-800 hover:text-slate-300"
            }`}
          >
            {labels[t]} <span className="ml-1 tabular-nums opacity-70">{counts[t]}</span>
          </button>
        ))}
      </div>

      <Rows report={report} tab={tab} all={all} />

      {counts[tab] > PREVIEW_ROWS && (
        <button onClick={() => setAll((v) => !v)} className="mt-2 text-xs font-medium text-cyan-500 hover:text-cyan-400 hover:underline">
          {all ? "Show fewer" : `Show all ${counts[tab]}`}
        </button>
      )}
    </Card>
  );
}
