import { FormEvent, ReactNode, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { accountApi, ApiKey, HttpError, KeyScope, Project, projectsApi } from "../api/client";
import { clearSession, SessionUser } from "../api/auth";
import { useProjects, useServices } from "../hooks/useMetrics";
import { Card } from "../components/Card";

const inputCls =
  "rounded-md border border-slate-800 bg-slate-950 px-3 py-1.5 text-sm text-slate-200 focus:border-cyan-600/60 focus:outline-none";
const buttonCls =
  "rounded-md bg-cyan-600/15 px-3 py-1.5 text-xs font-semibold text-cyan-300 ring-1 ring-cyan-600/30 hover:bg-cyan-600/25 disabled:cursor-not-allowed disabled:opacity-50";
const quietButtonCls = "rounded-md px-2 py-1 text-xs text-slate-500 ring-1 ring-slate-800 hover:text-slate-200";

function errorText(error: unknown): string {
  return error instanceof HttpError || error instanceof Error ? error.message : "Something went wrong.";
}

function ErrorLine({ error }: { error: unknown }) {
  if (!error) return null;
  return <p role="alert" className="mt-2 text-xs text-red-400">{errorText(error)}</p>;
}

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "never";
}

// --- account -------------------------------------------------------------------

function AccountSection({ user }: { user: SessionUser }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const change = useMutation({
    mutationFn: () => accountApi.changePassword(current, next),
    // The collector ends every session of this user: sign in again.
    onSuccess: () => setTimeout(clearSession, 1500),
  });
  const mismatch = repeat.length > 0 && next !== repeat;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!mismatch) change.mutate();
  }

  return (
    <Card title="Account">
      <p className="mb-4 text-xs text-slate-500">
        Signed in as <span className="font-mono text-slate-300">{user.username}</span>
        {user.is_admin ? " (admin of every project)" : ""}.
      </p>
      <form onSubmit={submit} className="grid max-w-md gap-2">
        <input type="password" autoComplete="current-password" placeholder="Current password" value={current}
          onChange={(e) => setCurrent(e.target.value)} className={inputCls} />
        <input type="password" autoComplete="new-password" placeholder="New password (12+ characters)" value={next}
          onChange={(e) => setNext(e.target.value)} className={inputCls} />
        <input type="password" autoComplete="new-password" placeholder="Repeat the new password" value={repeat}
          onChange={(e) => setRepeat(e.target.value)} className={inputCls} />
        {mismatch && <p className="text-xs text-amber-300">The new passwords don't match.</p>}
        <div>
          <button type="submit" className={buttonCls}
            disabled={change.isPending || change.isSuccess || !current || next.length < 12 || next !== repeat}>
            {change.isPending ? "Changing…" : "Change password"}
          </button>
        </div>
        {change.isSuccess && (
          <p className="text-xs text-emerald-400">Password changed. Every session was signed out — sign in again.</p>
        )}
        <ErrorLine error={change.error} />
      </form>
    </Card>
  );
}

// --- keys ----------------------------------------------------------------------

const SCOPES: { scope: KeyScope; hint: string }[] = [
  { scope: "ingest", hint: "send events (SDKs, OTLP)" },
  { scope: "read", hint: "read this project's data" },
  { scope: "admin", hint: "SLOs, specs, this project's keys" },
];

function NewKey({ secret, onDone }: { secret: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
      <p className="mb-2 text-xs text-amber-200">Copy this key now — it is shown only once and can't be recovered.</p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded bg-slate-950 px-2 py-1 font-mono text-xs text-slate-100">{secret}</code>
        <button
          className={buttonCls}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(secret);
              setCopied(true);
            } catch {
              setCopied(false);
            }
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <button className={quietButtonCls} onClick={onDone}>Done</button>
      </div>
    </div>
  );
}

function KeyRow({ apiKey, onRevoked }: { apiKey: ApiKey; onRevoked: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const revoke = useMutation({ mutationFn: () => projectsApi.revokeKey(apiKey.id), onSuccess: onRevoked });
  const revoked = apiKey.revoked_at !== null;
  return (
    <li className={`flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-xs ${revoked ? "opacity-50" : ""}`}>
      <span className="font-medium text-slate-200">{apiKey.name}</span>
      <code className="font-mono text-slate-500">{apiKey.prefix}…</code>
      <span className="flex gap-1">
        {apiKey.scopes.map((s) => (
          <span key={s} className="rounded bg-slate-800 px-1.5 py-px text-[10px] font-semibold text-slate-300">{s}</span>
        ))}
      </span>
      <span className="text-slate-600">used {when(apiKey.last_used_at)}</span>
      <span className="flex-1" />
      {revoked ? (
        <span className="text-[11px] text-slate-500">revoked {when(apiKey.revoked_at)}</span>
      ) : confirming ? (
        <span className="flex items-center gap-2">
          <button className="rounded-md bg-red-500/15 px-2 py-1 text-xs font-semibold text-red-300 ring-1 ring-red-500/30"
            disabled={revoke.isPending} onClick={() => revoke.mutate()}>
            Confirm revoke
          </button>
          <button className={quietButtonCls} onClick={() => setConfirming(false)}>Cancel</button>
        </span>
      ) : (
        <button className={quietButtonCls} onClick={() => setConfirming(true)}>Revoke</button>
      )}
      <ErrorLine error={revoke.error} />
    </li>
  );
}

function KeysTab({ project }: { project: Project }) {
  const qc = useQueryClient();
  const queryKey = ["project-keys", project.id];
  const { data, error } = useQuery({ queryKey, queryFn: () => projectsApi.keys(project.id), retry: false });
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<KeyScope[]>(["ingest"]);
  const [secret, setSecret] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => projectsApi.createKey(project.id, name.trim(), scopes),
    onSuccess: (key) => {
      setSecret(key.key);
      setName("");
      qc.invalidateQueries({ queryKey });
    },
  });

  if (error instanceof HttpError && error.status === 403) {
    return <p className="text-xs text-slate-500">Only this project's admins can see its keys.</p>;
  }

  return (
    <div>
      {secret && <NewKey secret={secret} onDone={() => setSecret(null)} />}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="mb-3 flex flex-wrap items-center gap-3"
      >
        <input placeholder="Key name, e.g. checkout-api prod" value={name} maxLength={128}
          onChange={(e) => setName(e.target.value)} className={`${inputCls} min-w-[220px] flex-1`} />
        {SCOPES.map(({ scope, hint }) => (
          <label key={scope} className="flex items-center gap-1.5 text-xs text-slate-400" title={hint}>
            <input type="checkbox" checked={scopes.includes(scope)}
              onChange={(e) => setScopes((cur) => (e.target.checked ? [...cur, scope] : cur.filter((s) => s !== scope)))} />
            {scope}
          </label>
        ))}
        <button type="submit" className={buttonCls} disabled={create.isPending || !name.trim() || scopes.length === 0}>
          Create key
        </button>
      </form>
      <ErrorLine error={create.error} />
      <ErrorLine error={error} />
      {data && data.keys.length === 0 && <p className="text-xs text-slate-600">No keys yet.</p>}
      <ul className="divide-y divide-slate-800/60">
        {data?.keys.map((k) => (
          <KeyRow key={k.id} apiKey={k} onRevoked={() => qc.invalidateQueries({ queryKey })} />
        ))}
      </ul>
    </div>
  );
}

// --- members -------------------------------------------------------------------

function MembersTab({ project }: { project: Project }) {
  const qc = useQueryClient();
  const queryKey = ["project-members", project.id];
  const { data, error } = useQuery({ queryKey, queryFn: () => projectsApi.members(project.id), retry: false });
  const [username, setUsername] = useState("");
  const add = useMutation({
    mutationFn: () => projectsApi.addMember(project.id, username.trim()),
    onSuccess: () => {
      setUsername("");
      qc.invalidateQueries({ queryKey });
    },
  });
  const remove = useMutation({
    mutationFn: (userId: number) => projectsApi.removeMember(project.id, userId),
    onSuccess: () => qc.invalidateQueries({ queryKey }),
  });

  return (
    <div>
      <p className="mb-3 text-xs text-slate-500">
        Members can read this project's services. Admins can read every project without being added.
      </p>
      <form onSubmit={(e) => { e.preventDefault(); add.mutate(); }} className="mb-3 flex flex-wrap gap-2">
        <input placeholder="username" value={username} onChange={(e) => setUsername(e.target.value)}
          className={`${inputCls} min-w-[180px]`} />
        <button type="submit" className={buttonCls} disabled={add.isPending || !username.trim()}>Add member</button>
      </form>
      <ErrorLine error={add.error} />
      <ErrorLine error={error} />
      {data && data.members.length === 0 && <p className="text-xs text-slate-600">No members yet.</p>}
      <ul className="divide-y divide-slate-800/60">
        {data?.members.map((m) => (
          <li key={m.user_id} className="flex items-center gap-3 py-2 text-xs">
            <span className="font-mono text-slate-200">{m.username}</span>
            {m.is_admin && <span className="text-[10px] text-slate-500">admin</span>}
            <span className="flex-1" />
            <button className={quietButtonCls} disabled={remove.isPending} onClick={() => remove.mutate(m.user_id)}>
              Remove
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- services ------------------------------------------------------------------

function ServicesTab({ project, isAdmin }: { project: Project; isAdmin: boolean }) {
  const qc = useQueryClient();
  const { data: all } = useServices();
  const [service, setService] = useState("");
  const move = useMutation({
    mutationFn: () => projectsApi.moveService(project.id, service),
    onSuccess: () => {
      setService("");
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const candidates = (all?.services ?? []).filter((s) => !project.services.includes(s));

  return (
    <div>
      {project.services.length === 0 ? (
        <p className="mb-3 text-xs text-slate-600">
          No services yet. A service joins this project when one of its ingest keys sends its first events.
        </p>
      ) : (
        <ul className="mb-3 flex flex-wrap gap-1.5">
          {project.services.map((s) => (
            <li key={s} className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs text-slate-300">{s}</li>
          ))}
        </ul>
      )}
      {isAdmin && candidates.length > 0 && (
        <form onSubmit={(e) => { e.preventDefault(); move.mutate(); }} className="flex flex-wrap items-center gap-2">
          <select value={service} onChange={(e) => setService(e.target.value)} className={inputCls}>
            <option value="">Move a service here…</option>
            {candidates.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <button type="submit" className={buttonCls} disabled={!service || move.isPending}>Move</button>
          <span className="text-[11px] text-slate-600">Its history moves with it; keys of its old project lose access.</span>
        </form>
      )}
      <ErrorLine error={move.error} />
    </div>
  );
}

// --- projects --------------------------------------------------------------------

type Tab = "services" | "keys" | "members";

function ProjectCard({ project, isAdmin }: { project: Project; isAdmin: boolean }) {
  const [tab, setTab] = useState<Tab>("services");
  const tabs: { id: Tab; label: string }[] = [
    { id: "services", label: `Services (${project.services.length})` },
    ...(isAdmin ? [{ id: "keys" as Tab, label: "API keys" }, { id: "members" as Tab, label: "Members" }] : []),
  ];
  const content: Record<Tab, ReactNode> = {
    services: <ServicesTab project={project} isAdmin={isAdmin} />,
    keys: <KeysTab project={project} />,
    members: <MembersTab project={project} />,
  };
  return (
    <Card title={`${project.name} · ${project.slug}`}>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`rounded-md px-2.5 py-1 text-[11px] font-semibold ring-1 transition-colors ${
              tab === t.id ? "bg-cyan-600/15 text-cyan-300 ring-cyan-600/30" : "text-slate-500 ring-slate-800 hover:text-slate-300"
            }`}>
            {t.label}
          </button>
        ))}
      </div>
      {content[tab]}
    </Card>
  );
}

function CreateProject() {
  const qc = useQueryClient();
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => projectsApi.create(slug.trim().toLowerCase(), name.trim() || slug.trim()),
    onSuccess: () => {
      setSlug("");
      setName("");
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  return (
    <Card title="New project">
      <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }} className="flex flex-wrap gap-2">
        <input placeholder="slug, e.g. payments" value={slug} onChange={(e) => setSlug(e.target.value)}
          className={`${inputCls} w-48 font-mono`} maxLength={63} />
        <input placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)}
          className={`${inputCls} w-56`} maxLength={128} />
        <button type="submit" className={buttonCls} disabled={!slug.trim() || create.isPending}>Create project</button>
      </form>
      <ErrorLine error={create.error} />
    </Card>
  );
}

export function SettingsPage({ user, onBack }: { user: SessionUser; onBack: () => void }) {
  const { data, error, isLoading } = useProjects();
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className={quietButtonCls}>← Dashboard</button>
        <h2 className="text-sm font-semibold text-slate-200">Settings</h2>
      </div>
      <AccountSection user={user} />
      {user.is_admin && <CreateProject />}
      {isLoading && <div className="h-24 animate-pulse rounded-xl bg-slate-900" />}
      <ErrorLine error={error} />
      {data?.projects.map((p) => <ProjectCard key={p.id} project={p} isAdmin={user.is_admin} />)}
      {data && data.projects.length === 0 && (
        <p className="text-xs text-slate-500">You aren't a member of any project yet; ask an admin to add you.</p>
      )}
    </div>
  );
}
