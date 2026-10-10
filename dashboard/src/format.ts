export function formatBucketLabel(iso: string, includeDate = false): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (includeDate) {
    return d.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** True when the buckets fall on more than one local calendar day, i.e. a
 * bare HH:MM axis label would be ambiguous. */
export function spansMultipleDays(buckets: string[]): boolean {
  if (buckets.length < 2) return false;
  const first = new Date(buckets[0]).toDateString();
  const last = new Date(buckets[buckets.length - 1]).toDateString();
  return first !== last;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(0)}ms`;
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  if (value < 100) return `$${value.toFixed(2)}`;
  return `$${Math.round(value).toLocaleString()}`;
}
