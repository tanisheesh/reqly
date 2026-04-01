import { ReactNode } from "react";

interface CardProps {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}

export function Card({ title, children, action }: CardProps) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 px-5 py-4">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-widest text-slate-500">
          {title}
        </h3>
        {action && <div className="flex items-center gap-2">{action}</div>}
      </div>
      {children}
    </div>
  );
}
