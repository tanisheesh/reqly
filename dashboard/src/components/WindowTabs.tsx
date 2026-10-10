import { UsageWindow } from "../api/client";

const WINDOWS: UsageWindow[] = ["24h", "7d", "30d"];

export function WindowTabs({ value, onChange }: { value: UsageWindow; onChange: (w: UsageWindow) => void }) {
  return (
    <div className="flex rounded-md ring-1 ring-slate-800">
      {WINDOWS.map((w) => (
        <button
          key={w}
          onClick={() => onChange(w)}
          className={`px-2 py-0.5 text-[11px] font-semibold transition-colors ${
            value === w ? "bg-cyan-600/15 text-cyan-300" : "text-slate-500 hover:text-slate-300"
          }`}
        >
          {w}
        </button>
      ))}
    </div>
  );
}
