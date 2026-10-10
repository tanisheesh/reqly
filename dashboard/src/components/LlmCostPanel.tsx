import { useState } from "react";
import { UsageWindow } from "../api/client";
import { useLlmUsage } from "../hooks/useMetrics";
import { Card } from "./Card";
import { WindowTabs } from "./WindowTabs";
import { formatUsd as usd } from "../format";

const ROWS = 8;

function compact(n: number) {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export function LlmCostPanel({ serviceName }: { serviceName: string }) {
  const [range, setRange] = useState<UsageWindow>("7d");
  const { data, isError } = useLlmUsage(serviceName, range);
  // Hidden until the app records LLM usage (reqly.record_llm_usage).
  const [seen, setSeen] = useState(false);
  const hasData = !!data && data.totals.llm_requests > 0;
  if (hasData && !seen) setSeen(true);
  if (isError || (!hasData && !seen)) return null;

  const maxCost = Math.max(...(data?.routes.map((r) => r.cost_usd) ?? [0]), 0);

  return (
    <Card title="LLM cost by route" action={<WindowTabs value={range} onChange={setRange} />}>
      {data && (
        <>
          <div className="mb-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className="font-mono text-2xl font-semibold text-slate-100">{usd(data.totals.cost_usd)}</span>
            <span className="text-[11px] text-slate-500">
              {compact(data.totals.input_tokens)} in · {compact(data.totals.output_tokens)} out ·{" "}
              {data.totals.llm_requests.toLocaleString()} requests used a model
            </span>
          </div>

          <ul className="space-y-2">
            {data.routes.slice(0, ROWS).map((r) => (
              <li key={r.route} className="text-xs">
                <div className="flex items-baseline gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-slate-300">{r.route}</span>
                  <span className="tabular-nums text-slate-500" title="estimated cost per 1,000 requests to this route">
                    {usd(r.cost_per_1k_requests)}/1k req
                  </span>
                  <span className="w-20 text-right font-mono tabular-nums text-slate-200">{usd(r.cost_usd)}</span>
                </div>
                <div className="mt-1 h-1 overflow-hidden rounded bg-slate-800">
                  <div className="h-full bg-violet-500/70" style={{ width: `${maxCost ? (100 * r.cost_usd) / maxCost : 0}%` }} />
                </div>
                <div className="mt-0.5 text-[11px] text-slate-600">
                  {r.models.map((m) => m.model).join(", ")} · {Math.round(r.tokens_per_llm_request).toLocaleString()} tokens/call
                </div>
              </li>
            ))}
          </ul>

          <p className="mt-3 text-[11px] text-slate-600">
            Estimated from list prices{data.prices_as_of ? ` (as of ${data.prices_as_of})` : ""}; edit them with
            LLM_PRICES_FILE.
            {data.unpriced_models.length > 0 && (
              <span className="text-amber-300/80">
                {" "}No price for {data.unpriced_models.map((m) => m.model).join(", ")} — not counted.
              </span>
            )}
          </p>
        </>
      )}
    </Card>
  );
}
