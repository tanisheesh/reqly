import { TimeWindow } from "../api/client";

const WINDOWS: { value: TimeWindow; label: string }[] = [
  { value: "1h", label: "1h" },
  { value: "6h", label: "6h" },
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
];

interface Props {
  value: TimeWindow;
  onChange: (window: TimeWindow) => void;
}

export function TimeRangePicker({ value, onChange }: Props) {
  return (
    <div className="flex h-8 gap-px overflow-hidden rounded-md border border-slate-700 bg-slate-800">
      {WINDOWS.map((w) => (
        <button
          key={w.value}
          onClick={() => onChange(w.value)}
          className={`px-3 text-xs font-medium transition-colors ${
            value === w.value
              ? "bg-cyan-600 text-white"
              : "text-slate-400 hover:bg-slate-700 hover:text-slate-200"
          }`}
        >
          {w.label}
        </button>
      ))}
    </div>
  );
}
