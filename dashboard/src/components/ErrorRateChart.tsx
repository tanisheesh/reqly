import {
  Area,
  CartesianGrid,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ErrorRatePoint } from "../api/client";
import { Card } from "./Card";
import { formatBucketLabel, formatPercent } from "../format";

const ERROR_THRESHOLD = 5; // 5%

export function ErrorRateChart({ data }: { data: ErrorRatePoint[] }) {
  const chartData = data.map((d) => ({
    ...d,
    label: formatBucketLabel(d.bucket),
    error_rate_pct: d.error_rate * 100,
  }));

  const latest = chartData.at(-1)?.error_rate ?? null;

  return (
    <Card
      title="Error rate"
      action={
        latest !== null ? (
          <span
            className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ${
              latest * 100 > ERROR_THRESHOLD
                ? "bg-red-500/10 text-red-400 ring-1 ring-red-500/20"
                : "bg-emerald-500/10 text-emerald-400 ring-1 ring-emerald-500/20"
            }`}
          >
            {formatPercent(latest)} now
          </span>
        ) : null
      }
    >
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis
            dataKey="label"
            stroke="#334155"
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            stroke="#334155"
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            unit="%"
          />
          <Tooltip
            contentStyle={{
              background: "#0f172a",
              border: "1px solid #1e293b",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#94a3b8", marginBottom: 4 }}
            formatter={(value) => [`${Number(value ?? 0).toFixed(2)}%`, "error rate"]}
          />
          <ReferenceLine
            y={ERROR_THRESHOLD}
            stroke="#f87171"
            strokeDasharray="4 4"
            strokeOpacity={0.5}
            label={{ value: "5% threshold", fill: "#f87171", fontSize: 10, dx: -4 }}
          />
          <Area
            type="monotone"
            dataKey="error_rate_pct"
            stroke="#f87171"
            strokeWidth={1.5}
            fill="#f87171"
            fillOpacity={0.12}
            name="error rate"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </Card>
  );
}
