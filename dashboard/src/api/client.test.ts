import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { saveSession, SIGNED_OUT_EVENT, sessionToken } from "./auth";
import { api, authApi, HttpError } from "./client";

type FetchCall = [string, RequestInit | undefined];

function mockFetch(status: number, body: unknown = {}) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function headersOf(call: FetchCall): Record<string, string> {
  return (call[1]?.headers ?? {}) as Record<string, string>;
}

const future = () => new Date(Date.now() + 3_600_000).toISOString();

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends the read key when nobody is signed in", async () => {
    const fetchMock = mockFetch(200, { services: ["shop"] });
    expect(await api.listServices()).toEqual({ services: ["shop"] });
    const call = fetchMock.mock.calls[0] as FetchCall;
    expect(call[0]).toMatch(/\/v1\/services$/);
    expect(headersOf(call)["X-Reqly-Key"]).toBeTruthy();
    expect(headersOf(call).Authorization).toBeUndefined();
  });

  it("sends the session token instead once signed in", async () => {
    saveSession("tok-123", future(), { id: 1, username: "ana", is_admin: false });
    const fetchMock = mockFetch(200, { services: [] });
    await api.listServices();
    const headers = headersOf(fetchMock.mock.calls[0] as FetchCall);
    expect(headers.Authorization).toBe("Bearer tok-123");
    expect(headers["X-Reqly-Key"]).toBeUndefined();
  });

  it("drops an expired session on 401 and signals sign-out", async () => {
    saveSession("tok-old", future(), { id: 1, username: "ana", is_admin: false });
    mockFetch(401, { detail: "session expired" });
    const signedOut = vi.fn();
    window.addEventListener(SIGNED_OUT_EVENT, signedOut);
    const failure = await api.listServices().catch((e: unknown) => e);
    window.removeEventListener(SIGNED_OUT_EVENT, signedOut);
    expect(failure).toBeInstanceOf(HttpError);
    expect((failure as HttpError).status).toBe(401);
    expect(sessionToken()).toBeNull();
    expect(signedOut).toHaveBeenCalledOnce();
  });

  it("puts the route and environment filters in the summary query", async () => {
    const fetchMock = mockFetch(200, {});
    await api.getMetricsSummary("shop api", "/orders/{id}", "24h", "prod");
    const url = new URL((fetchMock.mock.calls[0] as FetchCall)[0]);
    expect(url.pathname).toBe("/v1/metrics/summary");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      service_name: "shop api", window: "24h", route: "/orders/{id}", environment: "prod",
    });
  });

  it("treats a collector without auth endpoints as public", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network down"); }));
    expect(await authApi.config()).toEqual({ public_dashboard: true, login: false });
  });
});
