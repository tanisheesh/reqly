import { FormEvent, useState } from "react";
import { authApi, HttpError } from "../api/client";
import { saveSession, SessionUser } from "../api/auth";

function ReqlyMark() {
  return (
    <svg width={22} height={22} viewBox="0 0 24 24" fill="none" stroke="#06b6d4" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 12h3l3-8 4 16 3-10 2 2h5" />
    </svg>
  );
}

export function LoginPage({ onSignedIn, onCancel }: { onSignedIn: (user: SessionUser) => void; onCancel?: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const result = await authApi.login(username.trim(), password);
      saveSession(result.token, result.expires_at, result.user);
      setPassword("");
      onSignedIn(result.user);
    } catch (err) {
      setError(err instanceof HttpError ? err.message : "Couldn't reach the collector.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-slate-800 bg-slate-900/60 p-6">
        <div className="mb-6 flex items-center gap-2 text-white">
          <ReqlyMark />
          <span className="text-lg font-semibold tracking-tight">Reqly</span>
        </div>
        <h1 className="mb-1 text-sm font-semibold text-slate-200">Sign in</h1>
        <p className="mb-5 text-xs text-slate-500">Use the account your Reqly admin created for you.</p>

        <label className="mb-1 block text-[11px] font-semibold uppercase tracking-widest text-slate-500" htmlFor="username">
          Username
        </label>
        <input
          id="username"
          autoComplete="username"
          autoFocus
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mb-4 w-full rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-200 focus:border-cyan-600/60 focus:outline-none"
        />
        <label className="mb-1 block text-[11px] font-semibold uppercase tracking-widest text-slate-500" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-4 w-full rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-200 focus:border-cyan-600/60 focus:outline-none"
        />

        {error && (
          <p role="alert" className="mb-4 rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={pending || !username.trim() || !password}
          className="w-full rounded-md bg-cyan-600/20 px-4 py-2 text-sm font-semibold text-cyan-300 ring-1 ring-cyan-600/40 transition-colors hover:bg-cyan-600/30 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? "Signing in…" : "Sign in"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="mt-3 w-full text-xs text-slate-500 hover:text-slate-300">
            Back to the dashboard
          </button>
        )}
      </form>
    </div>
  );
}
