import { useState } from "react";
import { ServiceSelector } from "../components/ServiceSelector";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { LatencyChart } from "../components/LatencyChart";
import { ErrorRateChart } from "../components/ErrorRateChart";
import { StatusDistributionChart } from "../components/StatusDistributionChart";
import { TopRoutesTable } from "../components/TopRoutesTable";
import { InsightsPanel } from "../components/InsightsPanel";
import { useMetricsSummary } from "../hooks/useMetrics";
import { TimeWindow } from "../api/client";

function ReqlyIcon({ size = 18 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="#06b6d4"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 12h3l3-8 4 16 3-10 2 2h5" />
    </svg>
  );
}

function KpiTile({
  label,
  value,
  unit = "",
  color = "cyan",
}: {
  label: string;
  value: string | number | null;
  unit?: string;
  color?: "cyan" | "red" | "green" | "amber";
}) {
  const colorMap = {
    cyan: "text-cyan-400",
    red: "text-red-400",
    green: "text-emerald-400",
    amber: "text-amber-400",
  };
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 px-4 py-3">
      <p className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-slate-500">
        {label}
      </p>
      <p className={`font-mono text-2xl font-semibold tracking-tight ${colorMap[color]}`}>
        {value === null ? "—" : value}
        {value !== null && unit && (
          <span className="ml-1 text-sm font-normal text-slate-500">{unit}</span>
        )}
      </p>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-800 py-20 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-slate-800 bg-slate-900">
        <ReqlyIcon size={22} />
      </div>
      <p className="mb-1 text-sm font-medium text-slate-300">No service selected</p>
      <p className="max-w-xs text-xs leading-relaxed text-slate-600">
        Pick a service from the dropdown above. If the list is empty, make sure the collector
        is running: <code className="rounded bg-slate-800 px-1 py-0.5 font-mono text-slate-400">docker compose up -d</code>
      </p>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-[74px] animate-pulse rounded-lg bg-slate-900" />
        ))}
      </div>
      {[260, 260, 200].map((h, i) => (
        <div
          key={i}
          className="animate-pulse rounded-lg bg-slate-900"
          style={{ height: h }}
        />
      ))}
    </div>
  );
}

export function Dashboard() {
  const [serviceName, setServiceName] = useState<string | null>(null);
  const [route, setRoute] = useState<string | null>(null);
  const [timeWindow, setWindowValue] = useState<TimeWindow>("1h");

  const { data: summary, isLoading } = useMetricsSummary(serviceName, route, timeWindow);

  const year = new Date().getFullYear();

  const latestP95 = summary?.latency.at(-1)?.p95_ms?.toFixed(0) ?? null;
  const lastErrPoint = summary?.error_rate.at(-1);
  const latestErrPct =
    lastErrPoint?.error_rate != null
      ? (lastErrPoint.error_rate * 100).toFixed(2)
      : null;
  const errColor =
    latestErrPct !== null && parseFloat(latestErrPct) > 5 ? "red" : "green";

  return (
    <div className="flex min-h-screen flex-col bg-slate-950">
      {/* ── Sticky header ── */}
      <header className="sticky top-0 z-20 border-b border-slate-800/70 bg-slate-950/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-screen-xl items-center gap-4 px-4 py-3 sm:px-6">
          {/* Logo */}
          <button
            onClick={() => { setServiceName(null); setRoute(null); }}
            className="flex shrink-0 items-center gap-2 text-white"
          >
            <ReqlyIcon size={20} />
            <span className="text-[15px] font-semibold tracking-tight">Reqly</span>
          </button>

          {/* Divider */}
          <span className="hidden h-5 w-px bg-slate-800 sm:block" />

          {/* Service name breadcrumb */}
          {serviceName && (
            <span className="hidden truncate font-mono text-xs text-slate-500 sm:block">
              {serviceName}{route ? ` · ${route}` : ""}
            </span>
          )}

          {/* Spacer */}
          <div className="flex-1" />

          {/* Controls */}
          <div className="flex flex-wrap items-center justify-end gap-3">
            <ServiceSelector
              serviceName={serviceName}
              route={route}
              onServiceChange={(s) => { setServiceName(s); setRoute(null); }}
              onRouteChange={setRoute}
            />
            <TimeRangePicker value={timeWindow} onChange={setWindowValue} />
          </div>
        </div>
      </header>

      {/* ── Main content ── */}
      <main className="mx-auto w-full max-w-screen-xl flex-1 px-4 py-6 sm:px-6">
        {!serviceName && <EmptyState />}
        {serviceName && isLoading && <LoadingState />}

        {serviceName && summary && (
          <div className="space-y-4">
            {/* KPI strip */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <KpiTile
                label="Requests / min"
                value={summary.request_rate.requests_per_minute}
                color="cyan"
              />
              <KpiTile
                label="p95 latency"
                value={latestP95}
                unit="ms"
                color="amber"
              />
              <KpiTile
                label="Error rate"
                value={latestErrPct}
                unit="%"
                color={errColor}
              />
            </div>

            {/* Latency — full width */}
            <LatencyChart data={summary.latency} />

            {/* Error rate + Status side by side */}
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <ErrorRateChart data={summary.error_rate} />
              </div>
              <div className="lg:col-span-1">
                <StatusDistributionChart data={summary.status_distribution} />
              </div>
            </div>

            {/* Top routes table */}
            <TopRoutesTable data={summary.top_routes} />

            {/* AI Insights */}
            <InsightsPanel serviceName={serviceName} />
          </div>
        )}
      </main>

      {/* ── Footer ── */}
      <footer className="mt-6 border-t border-slate-800/60">
        <div className="mx-auto flex max-w-screen-xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-5 text-xs text-slate-600">
            <div className="flex items-center gap-1.5">
              <ReqlyIcon size={13} />
              <span>Reqly</span>
              <span className="text-slate-800">·</span>
              <span>GPL-3.0</span>
            </div>
            <a
              href="https://github.com/tanisheesh/reqly"
              target="_blank"
              rel="noopener noreferrer"
              className="transition-colors hover:text-slate-400"
            >
              GitHub
            </a>
            <a
              href="http://localhost:8000/docs"
              target="_blank"
              rel="noopener noreferrer"
              className="transition-colors hover:text-slate-400"
            >
              API Docs
            </a>
          </div>
          <p className="text-xs text-slate-700">
            Copyright {year}&nbsp;&nbsp;|&nbsp;&nbsp;Made with{" "}
            <span className="text-red-500/80">♥</span> by{" "}
            <a
              href="https://tanisheesh.in/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-600 transition-colors hover:text-slate-300"
            >
              Tanish Poddar
            </a>
          </p>
        </div>
      </footer>
    </div>
  );
}
