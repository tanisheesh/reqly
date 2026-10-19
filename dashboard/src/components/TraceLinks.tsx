/** The trace ids an alert points at; links when the collector has TRACE_URL_TEMPLATE. */
export function TraceLinks({ ids, urls }: { ids?: string[]; urls?: string[] }) {
  if (!ids || ids.length === 0) return null;
  return (
    <div className="text-slate-500">
      traces:{" "}
      {ids.map((id, i) => (
        <span key={id} className="font-mono">
          {i > 0 && ", "}
          {urls?.[i] ? (
            <a href={urls[i]} target="_blank" rel="noreferrer" className="text-sky-400 hover:underline">
              {id.slice(0, 12)}…
            </a>
          ) : (
            <span title="copy the full id" className="select-all">
              {id}
            </span>
          )}
        </span>
      ))}
    </div>
  );
}
