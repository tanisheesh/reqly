import { useState } from "react";
import { UsageWindow } from "../api/client";
import { formatMs, formatPercent } from "../format";
import { useConsumers } from "../hooks/useMetrics";
import { Card } from "./Card";
import { WindowTabs } from "./WindowTabs";

const ROWS = 8;
const ERROR_THRESHOLD = 0.05;

export function ConsumersPanel({ serviceName }: { serviceName: string }) {
  const [range, setRange] = useState<UsageWindow>("7d");
  const { data, isError } = useConsumers(serviceName, range);
  // Opt-in: hidden until the SDK sends consumer ids (consumer_header / consumer=).
  // A window without consumers keeps the card once it has been shown.
  const [seen, setSeen] = useState(false);
  const hasData = !!data && data.requests_with_consumer > 0;
  if (hasData && !seen) setSeen(true);
  if (isError || (!hasData && !seen)) return null;

  return (
    <Card title="Top consumers" action={<WindowTabs value={range} onChange={setRange} />}>
      {data && (
        <p className="mb-3 text-[11px] text-slate-500">
          {data.consumers.toLocaleString()} consumers ·{" "}
          {formatPercent(data.requests ? data.requests_with_consumer / data.requests : null)} of requests identified
        </p>
      )}
      {data && data.top.length === 0 && <p className="py-4 text-center text-xs text-slate-600">No identified traffic in this window.</p>}
      {data && data.top.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[460px] text-xs">
            <thead>
              <tr className="border-b border-slate-800 text-left text-slate-500">
                <th className="pb-2 pr-3 font-semibold">Consumer</th>
                <th className="pb-2 pr-3 text-right font-semibold">Requests</th>
                <th className="pb-2 pr-3 text-right font-semibold">Share</th>
                <th className="pb-2 pr-3 text-right font-semibold">Errors</th>
                <th className="pb-2 pr-3 text-right font-semibold">p95</th>
                <th className="pb-2 text-right font-semibold">Routes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {data.top.slice(0, ROWS).map((c) => (
                <tr key={c.consumer_id} className="text-slate-400">
                  <td className="py-2 pr-3 font-mono text-slate-300">{c.consumer_id}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">{c.requests.toLocaleString()}</td>
                  <td className="py-2 pr-3 text-right tabular-nums">{formatPercent(c.share_of_requests)}</td>
                  <td
                    className={`py-2 pr-3 text-right tabular-nums ${
                      (c.error_rate ?? 0) > ERROR_THRESHOLD ? "text-red-400" : "text-slate-400"
                    }`}
                  >
                    {formatPercent(c.error_rate)}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums">{c.p95_ms === null ? "—" : formatMs(c.p95_ms)}</td>
                  <td className="py-2 text-right tabular-nums">{c.routes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
