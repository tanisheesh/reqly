import { FormEvent, useState } from "react";
import { AskStep, HttpError } from "../api/client";
import { useAsk } from "../hooks/useInsights";
import { Card } from "./Card";

const SUGGESTIONS = [
  "Why did errors go up in the last 24 hours?",
  "Did the latest release make anything slower?",
  "Which route is slowest this week, and is it getting worse?",
  "Are we on track for our SLOs?",
];

function describeError(error: unknown): string {
  if (error instanceof HttpError) {
    if (error.status === 401) return "The collector rejected the dashboard's read key (401). Check VITE_READ_KEY.";
    if (error.status === 503) return "Ask Reqly isn't enabled on this collector — it needs GROQ_API_KEY.";
    if (error.status === 429) return `Rate limited: ${error.message}. Questions are capped at 5 per minute.`;
    return error.message;
  }
  return error instanceof Error ? error.message : "Unexpected error.";
}

// Models sometimes emphasise with **bold** despite being asked for plain
// text; show it as bold instead of literal asterisks (no HTML injection).
function renderAnswer(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
      <strong key={i} className="font-semibold text-slate-100">
        {part.slice(2, -2)}
      </strong>
    ) : (
      part
    )
  );
}

function formatArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

function Step({ step }: { step: AskStep }) {
  const error = typeof step.result.error === "string" ? step.result.error : null;
  return (
    <li className="font-mono text-[11px] leading-relaxed">
      <span className="text-cyan-400">{step.tool}</span>
      <span className="text-slate-500">({formatArgs(step.arguments)})</span>
      {error && <span className="text-amber-400"> → {error}</span>}
    </li>
  );
}

export function AskPanel({ serviceName }: { serviceName: string }) {
  const [question, setQuestion] = useState("");
  const [showSteps, setShowSteps] = useState(false);
  const ask = useAsk(serviceName);

  function submit(q: string) {
    const trimmed = q.trim();
    if (trimmed.length < 3 || ask.isPending) return;
    setQuestion(trimmed);
    setShowSteps(false);
    ask.mutate(trimmed);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit(question);
  }

  return (
    <Card title="Ask Reqly">
      <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={500}
          placeholder={`Ask about ${serviceName}, e.g. "why was /orders slow yesterday afternoon?"`}
          className="min-w-0 flex-1 rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-cyan-600/60 focus:outline-none"
        />
        <button
          type="submit"
          disabled={ask.isPending || question.trim().length < 3}
          className="flex items-center justify-center gap-1.5 rounded-md bg-cyan-600/15 px-4 py-2 text-xs font-semibold text-cyan-400 ring-1 ring-cyan-600/30 transition-colors hover:bg-cyan-600/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {ask.isPending ? (
            <>
              <span className="inline-block h-3 w-3 animate-spin rounded-full border border-current border-t-transparent" />
              Looking…
            </>
          ) : (
            "Ask"
          )}
        </button>
      </form>

      {!ask.data && !ask.isPending && !ask.isError && (
        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => submit(s)}
              className="rounded-full border border-slate-800 px-3 py-1 text-[11px] text-slate-400 transition-colors hover:border-slate-700 hover:text-slate-200"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {ask.isError && (
        <p className="mt-3 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          {describeError(ask.error)}
        </p>
      )}

      {ask.data && !ask.isPending && (
        <div className="mt-4 space-y-3">
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">{renderAnswer(ask.data.answer)}</div>
          {ask.data.steps.length > 0 && (
            <div>
              <button
                onClick={() => setShowSteps((v) => !v)}
                className="text-xs font-medium text-cyan-500 hover:text-cyan-400 hover:underline"
              >
                {showSteps ? "Hide" : "Show"} the {ask.data.steps.length} {ask.data.steps.length === 1 ? "query" : "queries"} behind this answer
              </button>
              {showSteps && (
                <ol className="mt-2 list-decimal space-y-1 pl-5 text-slate-500">
                  {ask.data.steps.map((step, i) => (
                    <Step key={i} step={step} />
                  ))}
                </ol>
              )}
            </div>
          )}
          {(ask.data.unverified_numbers ?? []).length > 0 && (
            <p className="rounded-md border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-300">
              Not found in the query results, so double-check:{" "}
              <span className="font-mono">{ask.data.unverified_numbers!.join(", ")}</span>. They may be
              derived by the model or mis-copied.
            </p>
          )}
          <p className="text-[11px] text-slate-600">
            Answered by {ask.data.model} from this collector's data. Check the numbers before acting on them.
          </p>
        </div>
      )}
    </Card>
  );
}
