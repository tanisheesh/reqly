import { useServices, useRoutes } from "../hooks/useMetrics";

interface Props {
  serviceName: string | null;
  route: string | null;
  onServiceChange: (service: string | null) => void;
  onRouteChange: (route: string | null) => void;
}

const selectCls =
  "h-8 rounded-md border border-slate-700 bg-slate-900 px-2.5 text-xs font-medium text-slate-200 " +
  "focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600/40 " +
  "disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer";

export function ServiceSelector({ serviceName, route, onServiceChange, onRouteChange }: Props) {
  const { data: servicesData, isLoading: servicesLoading } = useServices();
  const { data: routesData } = useRoutes(serviceName);

  const services = servicesData?.services ?? [];
  const routes = routesData?.routes ?? [];

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        className={selectCls}
        value={serviceName ?? ""}
        onChange={(e) => {
          onServiceChange(e.target.value || null);
          onRouteChange(null);
        }}
      >
        <option value="" disabled>
          {servicesLoading ? "Loading…" : "Select service"}
        </option>
        {services.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>

      <select
        className={`${selectCls} min-w-[130px]`}
        value={route ?? ""}
        disabled={!serviceName}
        onChange={(e) => onRouteChange(e.target.value || null)}
      >
        <option value="">All routes</option>
        {routes.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
    </div>
  );
}
