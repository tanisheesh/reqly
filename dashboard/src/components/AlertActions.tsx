import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { currentSession } from "../api/auth";
import { Alert, api } from "../api/client";

const SILENCE_HOURS = [1, 4, 24];

function until(iso: string): string {
  return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** On-call state of an alert, and (signed in) acknowledge / silence / mute. */
export function AlertActions({ alert }: { alert: Alert }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const action = useMutation({
    mutationFn: (run: () => Promise<unknown>) => run(),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
    onError: (e: Error) => setError(e.message),
  });
  const silenced = alert.silenced === true;
  // The read key can't act on alerts; only signed-in users (or admin keys) can.
  const canAct = currentSession() !== null;

  const badges = [
    alert.acknowledged_by && `acknowledged by ${alert.acknowledged_by}`,
    silenced && `silenced until ${until(alert.silenced_until!)}`,
    alert.muted && "muted",
  ].filter(Boolean);
  if (!canAct && badges.length === 0) return null;

  const button =
    "rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:border-slate-500 hover:text-white disabled:opacity-50";
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2">
      {badges.map((b) => (
        <span key={b as string} className="rounded bg-slate-800 px-1.5 py-0.5 text-[11px] text-slate-400">
          {b}
        </span>
      ))}
      {canAct && !alert.acknowledged_at && (
        <button className={button} disabled={action.isPending} onClick={() => action.mutate(() => api.acknowledgeAlert(alert.id))}>
          Acknowledge
        </button>
      )}
      {canAct &&
        (silenced ? (
          <button className={button} disabled={action.isPending} onClick={() => action.mutate(() => api.silenceAlert(alert.id, 0))}>
            Unsilence
          </button>
        ) : (
          <select
            className="rounded border border-slate-700 bg-transparent px-1 py-0.5 text-[11px] text-slate-300"
            value=""
            disabled={action.isPending}
            onChange={(e) => action.mutate(() => api.silenceAlert(alert.id, Number(e.target.value)))}
            aria-label="Silence this alert"
          >
            <option value="" disabled>
              Silence…
            </option>
            {SILENCE_HOURS.map((h) => (
              <option key={h} value={h}>
                {h}h
              </option>
            ))}
          </select>
        ))}
      {canAct && !alert.muted && (
        <button
          className={button}
          disabled={action.isPending}
          title="No notifications for this route for 24 hours"
          onClick={() => action.mutate(() => api.muteAlerts(alert.service_name, alert.route, 24, "muted from the dashboard"))}
        >
          Mute route 24h
        </button>
      )}
      {error && <span className="text-[11px] text-red-400">{error}</span>}
    </div>
  );
}
