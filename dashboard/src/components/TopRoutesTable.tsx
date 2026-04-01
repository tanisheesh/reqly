import { useState } from "react";
import { TopRoute } from "../api/client";
import { Card } from "./Card";
import { formatMs, formatPercent } from "../format";

const ERROR_THRESHOLD_RAW = 0.05; // 5% — keep in sync with ERROR_THRESHOLD in ErrorRateChart.tsx

type SortKey = "request_count" | "p95_ms" | "error_rate";

function SortBtn({
  k,
  label,
  active,
  onSort,
}: {
  k: SortKey;
  label: string;
  active: boolean;
  onSort: (k: SortKey) => void;
}) {
  return (
    <button
      onClick={() => onSort(k)}
      className={`flex items-center gap-1 transition-colors ${
        active ? "text-cyan-400" : "text-slate-500 hover:text-slate-300"
      }`}
    >
      {label}
      {active && <span className="text-[10px]">↓</span>}
    </button>
  );
}

export function TopRoutesTable({ data }: { data: TopRoute[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("request_count");

  const sorted = [...data].sort((a, b) => (b[sortKey] ?? 0) - (a[sortKey] ?? 0));

  return (
    <Card title="Top routes">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[500px] text-xs">
          <thead>
            <tr className="border-b border-slate-800">
              <th className="pb-2 text-left font-semibold text-slate-500">Route</th>
              <th className="pb-2 text-right">
                <SortBtn k="request_count" label="Requests" active={sortKey === "request_count"} onSort={setSortKey} />
              </th>
              <th className="pb-2 text-right">
                <SortBtn k="p95_ms" label="p95" active={sortKey === "p95_ms"} onSort={setSortKey} />
              </th>
              <th className="pb-2 text-right">
                <SortBtn k="error_rate" label="Error rate" active={sortKey === "error_rate"} onSort={setSortKey} />
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {sorted.map((r) => (
              <tr key={r.route} className="group transition-colors hover:bg-slate-800/30">
                <td className="py-2.5 pr-4 font-mono text-slate-300">{r.route}</td>
                <td className="py-2.5 text-right tabular-nums text-slate-400">
                  {r.request_count.toLocaleString()}
                </td>
                <td className="py-2.5 text-right tabular-nums text-slate-400">
                  {formatMs(r.p95_ms)}
                </td>
                <td
                  className={`py-2.5 text-right tabular-nums font-medium ${
                    (r.error_rate ?? 0) > ERROR_THRESHOLD_RAW ? "text-red-400" : "text-emerald-400"
                  }`}
                >
                  {formatPercent(r.error_rate)}
                </td>
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={4} className="py-10 text-center text-slate-600">
                  No traffic recorded in this window yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
