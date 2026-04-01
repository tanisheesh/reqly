import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { StatusDistributionPoint } from "../api/client";
import { Card } from "./Card";

function colorForStatus(status: number): string {
  if (status < 300) return "#34d399";
  if (status < 400) return "#38bdf8";
  if (status < 500) return "#fbbf24";
  return "#f87171";
}

export function StatusDistributionChart({ data }: { data: StatusDistributionPoint[] }) {
  const chartData = data.map((d) => ({ ...d, status: String(d.status_code) }));

  return (
    <Card title="Status codes">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart
          data={chartData}
          margin={{ top: 4, right: 4, left: -16, bottom: 0 }}
          barCategoryGap="35%"
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis
            dataKey="status"
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
          />
          <Tooltip
            contentStyle={{
              background: "#0f172a",
              border: "1px solid #1e293b",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#94a3b8", marginBottom: 4 }}
            cursor={{ fill: "rgba(255,255,255,0.03)" }}
          />
          <Bar dataKey="count" radius={[3, 3, 0, 0]}>
            {chartData.map((d) => (
              <Cell key={d.status} fill={colorForStatus(d.status_code)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  );
}
