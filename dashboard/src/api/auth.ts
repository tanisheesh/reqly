// Session token for a signed-in user. Kept in localStorage so a reload stays
// signed in; it is a bearer token, not a cookie, because the dashboard and
// the collector are usually on different sites (third-party cookies are
// blocked by most browsers). The dashboard renders no HTML from data
// (React escapes everything), which is what keeps the token out of reach of
// injected scripts.

const STORAGE_KEY = "reqly.session";
export const SIGNED_OUT_EVENT = "reqly:signed-out";

export interface SessionUser {
  id: number;
  username: string;
  is_admin: boolean;
}

interface StoredSession {
  token: string;
  expiresAt: string;
  user: SessionUser;
}

function read(): StoredSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as StoredSession;
    if (!session.token || Date.parse(session.expiresAt) <= Date.now()) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

export function currentSession(): StoredSession | null {
  return read();
}

export function saveSession(token: string, expiresAt: string, user: SessionUser): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ token, expiresAt, user }));
  } catch {
    // storage unavailable (private mode): the session lasts until reload
    memorySession = { token, expiresAt, user };
  }
}

let memorySession: StoredSession | null = null;

export function sessionToken(): string | null {
  return read()?.token ?? memorySession?.token ?? null;
}

/** Forgets the session and tells the app to show the sign-in page. */
export function clearSession(): void {
  memorySession = null;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
  window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
}
