import { useState } from "react";
import { ServiceSelector } from "../components/ServiceSelector";
import { TimeRangePicker } from "../components/TimeRangePicker";
import { LatencyChart } from "../components/LatencyChart";
import { ErrorRateChart } from "../components/ErrorRateChart";
import { StatusDistributionChart } from "../components/StatusDistributionChart";
import { TopRoutesTable } from "../components/TopRoutesTable";
import { ReleasesTable } from "../components/ReleasesTable";
import { ApiSurfacePanel } from "../components/ApiSurfacePanel";
import { ConsumersPanel } from "../components/ConsumersPanel";
import { LlmCostPanel } from "../components/LlmCostPanel";
import { AlertsBanner } from "../components/AlertsBanner";
import { SloPanel } from "../components/SloPanel";
import { InsightsPanel } from "../components/InsightsPanel";
import { AskPanel } from "../components/AskPanel";
import { useMetricsSummary, useProjects } from "../hooks/useMetrics";
import { COLLECTOR_URL, TimeWindow } from "../api/client";
import { SessionUser } from "../api/auth";
import { SettingsPage } from "./SettingsPage";

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

function Account({
  user,
  onSignIn,
  onSignOut,
  onSettings,
}: {
  user: SessionUser | null;
  onSignIn?: () => void;
  onSignOut: () => void;
  onSettings: () => void;
}) {
  if (user) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <span className="hidden text-slate-400 sm:inline" title={user.is_admin ? "admin" : undefined}>
          {user.username}
        </span>
        <button onClick={onSettings} className="rounded-md px-2 py-1 text-slate-500 ring-1 ring-slate-800 hover:text-slate-200">
          Settings
        </button>
        <button onClick={onSignOut} className="rounded-md px-2 py-1 text-slate-500 ring-1 ring-slate-800 hover:text-slate-200">
          Sign out
        </button>
      </div>
    );
  }
  if (!onSignIn) return null;
  return (
    <button onClick={onSignIn} className="rounded-md px-2 py-1 text-xs text-slate-500 ring-1 ring-slate-800 hover:text-slate-200">
      Sign in
    </button>
  );
}

export function Dashboard({
  user = null,
  onSignIn,
  onSignOut = () => {},
}: {
  user?: SessionUser | null;
  onSignIn?: () => void;
  onSignOut?: () => void;
} = {}) {
  const [serviceName, setServiceName] = useState<string | null>(null);
  const [route, setRoute] = useState<string | null>(null);
  const [timeWindow, setWindowValue] = useState<TimeWindow>("1h");
  const [requestedView, setView] = useState<"dashboard" | "settings">("dashboard");
  // Settings need a signed-in user; signing out there lands on the dashboard.
  const view = user ? requestedView : "dashboard";
  const [projectId, setProjectId] = useState<number | null>(null);
  const { data: projectsData } = useProjects();
  const projects = projectsData?.projects ?? [];
  const project = projects.find((p) => p.id === projectId) ?? null;

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
            {view === "dashboard" && projects.length > 1 && (
              <select
                aria-label="Project"
                value={projectId ?? ""}
                onChange={(e) => {
                  setProjectId(e.target.value ? Number(e.target.value) : null);
                  setServiceName(null);
                  setRoute(null);
                }}
                className="h-8 rounded-md border border-slate-700 bg-slate-900 px-2.5 text-xs font-medium text-slate-200 focus:border-cyan-600 focus:outline-none"
              >
                <option value="">All projects</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            )}
            {view === "dashboard" && (
              <>
                <ServiceSelector
                  serviceName={serviceName}
                  route={route}
                  onServiceChange={(s) => { setServiceName(s); setRoute(null); }}
                  onRouteChange={setRoute}
                  onlyServices={project?.services ?? null}
                />
                <TimeRangePicker value={timeWindow} onChange={setWindowValue} />
              </>
            )}
            <Account user={user} onSignIn={onSignIn} onSignOut={onSignOut} onSettings={() => setView("settings")} />
          </div>
        </div>
      </header>

      {/* ── Main content ── */}
      <main className="mx-auto w-full max-w-screen-xl flex-1 px-4 py-6 sm:px-6">
        {view === "settings" && user && <SettingsPage user={user} onBack={() => setView("dashboard")} />}
        {view === "dashboard" && (<>
        {!serviceName && <EmptyState />}
        {serviceName && isLoading && <LoadingState />}

        {serviceName && summary && (
          <div className="space-y-4">
            {/* Open alerts from the hourly check (hidden when none) */}
            <AlertsBanner serviceName={serviceName} />

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
                // The 1h window reads raw events in 5-minute buckets; longer
                // windows read the hourly aggregate, whose newest bucket is
                // the last completed hour -- so don't call it "now".
                label={timeWindow === "1h" ? "Error rate (5 min)" : "Error rate (last full hour)"}
                value={latestErrPct}
                unit="%"
                color={errColor}
              />
            </div>

            {/* SLOs (hidden when none are defined) */}
            <SloPanel serviceName={serviceName} />

            {/* Latency — full width */}
            <LatencyChart data={summary.latency} releases={summary.releases} />

            {/* Error rate + Status side by side */}
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <ErrorRateChart data={summary.error_rate} releases={summary.releases} />
              </div>
              <div className="lg:col-span-1">
                <StatusDistributionChart data={summary.status_distribution} />
              </div>
            </div>

            {/* Top routes table */}
            <TopRoutesTable data={summary.top_routes} />

            {/* Releases (deploy history + per-release health) */}
            <ReleasesTable serviceName={serviceName} />

            {/* Consumers and LLM cost (each hidden until the SDK sends that data) */}
            <div className="grid gap-4 empty:hidden xl:grid-cols-2">
              <ConsumersPanel key={`c-${serviceName}`} serviceName={serviceName} />
              <LlmCostPanel key={`l-${serviceName}`} serviceName={serviceName} />
            </div>

            {/* OpenAPI spec vs traffic (hidden when no spec is uploaded) */}
            <ApiSurfacePanel serviceName={serviceName} />

            {/* Ask Reqly (natural-language questions) */}
            <AskPanel key={serviceName} serviceName={serviceName} />

            {/* AI Insights */}
            <InsightsPanel serviceName={serviceName} />
          </div>
        )}
        </>)}
      </main>

      {/* ── Footer ── */}
      <footer className="mt-6 border-t border-slate-800/60">
        <div className="mx-auto flex max-w-screen-xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-5 text-xs text-slate-600">
            <div className="flex items-center gap-1.5">
              <ReqlyIcon size={13} />
              <span>Reqly</span>
              <span className="text-slate-800">·</span>
              <span>AGPL-3.0</span>
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
              href={`${COLLECTOR_URL}/docs`}
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
