import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { LatencyPoint } from "../api/client";
import { Card } from "./Card";
import { formatBucketLabel } from "../format";

export function LatencyChart({ data }: { data: LatencyPoint[] }) {
  const chartData = data.map((d) => ({
    ...d,
    label: formatBucketLabel(d.bucket),
  }));

  return (
    <Card title="Latency — p50 / p95 / p99">
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={chartData} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
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
            unit="ms"
          />
          <Tooltip
            contentStyle={{
              background: "#0f172a",
              border: "1px solid #1e293b",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#94a3b8", marginBottom: 4 }}
            itemStyle={{ color: "#cbd5e1" }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, paddingTop: 12, color: "#64748b" }}
          />
          <Line
            type="monotone"
            dataKey="p50_ms"
            stroke="#06b6d4"
            strokeWidth={1.5}
            dot={false}
            name="p50"
            activeDot={{ r: 3 }}
          />
          <Line
            type="monotone"
            dataKey="p95_ms"
            stroke="#fbbf24"
            strokeWidth={1.5}
            dot={false}
            name="p95"
            activeDot={{ r: 3 }}
          />
          <Line
            type="monotone"
            dataKey="p99_ms"
            stroke="#f87171"
            strokeWidth={1.5}
            dot={false}
            name="p99"
            activeDot={{ r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}
