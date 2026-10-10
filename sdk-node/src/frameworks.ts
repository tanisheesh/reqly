import { ReqlyClient } from "./client.js";
import { ReqlyOptions } from "./config.js";
import { LlmUsage, RequestInfo, requestStorage } from "./context.js";

type ClientOrOptions = ReqlyClient | ReqlyOptions | undefined;

function toClient(clientOrOptions: ClientOrOptions): ReqlyClient {
  return clientOrOptions instanceof ReqlyClient ? clientOrOptions : new ReqlyClient(clientOrOptions);
}

// Set on a request by the first Reqly middleware that sees it. A second
// one (registered twice, or two clients) leaves the request alone instead
// of recording it again.
const CLAIMED = Symbol.for("reqly.claimed");

function claim(target: object): boolean {
  const t = target as Record<symbol, unknown>;
  if (t[CLAIMED]) return false;
  t[CLAIMED] = true;
  return true;
}

function elapsedMs(start: bigint): number {
  return Number(process.hrtime.bigint() - start) / 1e6;
}

function lowerHeaders(headers: Record<string, unknown>): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(headers)) {
    out[key.toLowerCase()] = Array.isArray(value) ? value.join(", ") : value === undefined ? undefined : String(value);
  }
  return out;
}

function intOrUndefined(value: unknown): number | undefined {
  const n = Number(value);
  return value !== undefined && value !== null && value !== "" && Number.isInteger(n) && n >= 0 ? n : undefined;
}

// --- Express -------------------------------------------------------------------

/* Minimal structural types so Express isn't a dependency. */
interface ExpressRequest {
  method: string;
  path?: string;
  originalUrl?: string;
  baseUrl?: string;
  route?: { path?: unknown };
  headers: Record<string, unknown>;
}
interface ExpressResponse {
  statusCode: number;
  locals: Record<string, unknown>;
  getHeader(name: string): unknown;
  once(event: "finish" | "close", listener: () => void): unknown;
}
type Next = (err?: unknown) => void;

const EXPRESS_ERROR = "__reqlyErrorType";

/**
 * Express middleware (4 and 5). Register it before your routes:
 *
 *     const reqly = reqlyExpress({ serviceName: "checkout-api" });
 *     app.use(reqly);
 *     // ...routes...
 *     app.use(reqly.errorHandler); // optional: records the thrown error's type
 *
 * Routes are recorded as Express templates ("/users/:id", with the router's
 * mount path), requests no route matched as "__unmatched__".
 */
export function reqlyExpress(clientOrOptions?: ClientOrOptions) {
  const client = toClient(clientOrOptions);

  const middleware = (req: ExpressRequest, res: ExpressResponse, next: Next): void => {
    if (!claim(req)) return next();
    const start = process.hrtime.bigint();
    const usage = new LlmUsage();
    // The template is captured when Express assigns req.route: at that
    // moment req.baseUrl is the router's mount path. By the time the
    // response finishes, an error leaving a sub-router has already reset it.
    let matched: string | undefined;
    let routeValue: unknown;
    try {
      Object.defineProperty(req, "route", {
        configurable: true,
        enumerable: true,
        get: () => routeValue,
        set: (value: { path?: unknown } | undefined) => {
          routeValue = value;
          if (value && typeof value.path === "string") matched = (req.baseUrl ?? "") + value.path;
        },
      });
    } catch {
      // a frozen request object: fall back to reading req.route at the end
    }
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      try {
        const routePath = req.route?.path;
        const route = matched ?? (typeof routePath === "string" ? (req.baseUrl ?? "") + routePath : undefined);
        client.record({
          method: req.method,
          route: route === "" ? "/" : route,
          statusCode: res.statusCode,
          durationMs: elapsedMs(start),
          errorType: res.locals?.[EXPRESS_ERROR] as string | undefined,
          requestBytes: intOrUndefined(req.headers["content-length"]),
          responseBytes: intOrUndefined(res.getHeader("content-length")),
          requestInfo: (): RequestInfo => ({
            method: req.method,
            path: req.path ?? req.originalUrl ?? "/",
            headers: lowerHeaders(req.headers),
            raw: req,
          }),
          llm: usage.summary(),
        });
      } catch {
        // never reaches the app
      }
    };
    res.once("finish", finish);
    res.once("close", finish); // client aborted
    requestStorage.run(usage, () => next());
  };

  const errorHandler = (err: unknown, _req: ExpressRequest, res: ExpressResponse, next: Next): void => {
    if (res.locals) res.locals[EXPRESS_ERROR] = (err as Error | undefined)?.name ?? "Error";
    next(err);
  };

  return Object.assign(middleware, { client, errorHandler });
}

// --- Fastify -------------------------------------------------------------------

interface FastifyRequestLike {
  method: string;
  url: string;
  headers: Record<string, unknown>;
  routeOptions?: { url?: string };
  routerPath?: string; // Fastify < 4.10
  [key: symbol]: unknown;
}
interface FastifyReplyLike {
  statusCode: number;
  getHeader(name: string): unknown;
}
type Done = (err?: Error) => void;
interface FastifyLike {
  addHook(name: "onRequest", hook: (req: FastifyRequestLike, reply: FastifyReplyLike, done: Done) => void): unknown;
  addHook(name: "onError", hook: (req: FastifyRequestLike, reply: FastifyReplyLike, err: Error, done: Done) => void): unknown;
  addHook(name: "onResponse", hook: (req: FastifyRequestLike, reply: FastifyReplyLike, done: Done) => void): unknown;
}

const START = Symbol("reqly.start");
const USAGE = Symbol("reqly.usage");
const ERROR = Symbol("reqly.error");
const OWNER = Symbol("reqly.owner");

/**
 * Fastify plugin (4 and 5):
 *
 *     await app.register(reqlyFastify({ serviceName: "checkout-api" }));
 *
 * Hooks are added to the root instance, so every route is covered.
 */
export function reqlyFastify(clientOrOptions?: ClientOrOptions) {
  const client = toClient(clientOrOptions);

  const plugin = (fastify: FastifyLike, _opts: unknown, done: Done) => {
    fastify.addHook("onRequest", (req, _reply, next) => {
      if (!claim(req)) return next();
      req[OWNER] = plugin;
      req[START] = process.hrtime.bigint();
      const usage = new LlmUsage();
      req[USAGE] = usage;
      requestStorage.run(usage, () => next());
    });
    fastify.addHook("onError", (req, _reply, err, next) => {
      req[ERROR] = err?.name ?? "Error";
      next();
    });
    fastify.addHook("onResponse", (req, reply, next) => {
      if (req[OWNER] !== plugin) return next(); // another Reqly plugin records it
      try {
        const start = req[START] as bigint | undefined;
        if (start !== undefined) {
          client.record({
            method: req.method,
            route: req.routeOptions?.url ?? req.routerPath,
            statusCode: reply.statusCode,
            durationMs: elapsedMs(start),
            errorType: req[ERROR] as string | undefined,
            requestBytes: intOrUndefined(req.headers["content-length"]),
            responseBytes: intOrUndefined(reply.getHeader("content-length")),
            requestInfo: () => ({
              method: req.method,
              path: req.url.split("?")[0],
              headers: lowerHeaders(req.headers),
              raw: req,
            }),
            llm: (req[USAGE] as LlmUsage | undefined)?.summary(),
          });
        }
      } catch {
        // never reaches the app
      }
      next();
    });
    done();
  };
  // Same as fastify-plugin: don't encapsulate, so the hooks apply app-wide.
  (plugin as unknown as Record<symbol, unknown>)[Symbol.for("skip-override")] = true;
  (plugin as unknown as Record<symbol, unknown>)[Symbol.for("fastify.display-name")] = "reqly";
  return Object.assign(plugin, { client });
}

// --- Hono ----------------------------------------------------------------------

interface HonoRoute {
  path: string;
  method: string;
}
interface HonoContextLike {
  req: {
    method: string;
    path: string;
    raw: { headers: { forEach(cb: (value: string, key: string) => void): void } };
    header(name: string): string | undefined;
    matchedRoutes?: HonoRoute[];
    routeIndex?: number;
    routePath?: string;
  };
  res: { status: number; headers: { get(name: string): string | null } };
  error?: Error;
}

/**
 * The route of the handler that ran. After `next()` returns, routeIndex
 * points at the last handler Hono executed: the route handler, or -- when
 * nothing matched -- the last middleware, recognizable as an "ALL" route
 * with a wildcard path ("/*", "/api/*"), which means no route (404).
 */
function honoRoute(c: HonoContextLike): string | undefined {
  const routes = c.req.matchedRoutes ?? [];
  const index = typeof c.req.routeIndex === "number" ? c.req.routeIndex : routes.length - 1;
  const route = routes[index];
  if (!route) return undefined;
  if (route.method === "ALL" && route.path.includes("*")) return undefined;
  return route.path;
}

/**
 * Hono middleware (Node.js runtime):
 *
 *     app.use(reqlyHono({ serviceName: "checkout-api" }));
 *
 * The route is the matched handler's path ("/users/:id").
 */
export function reqlyHono(clientOrOptions?: ClientOrOptions) {
  const client = toClient(clientOrOptions);

  const middleware = async (c: HonoContextLike, next: () => Promise<void>): Promise<void> => {
    if (!claim(c.req.raw)) return next();
    const start = process.hrtime.bigint();
    const usage = new LlmUsage();
    try {
      await requestStorage.run(usage, () => next());
    } finally {
      try {
        const route = honoRoute(c);
        client.record({
          method: c.req.method,
          route,
          statusCode: c.res.status,
          durationMs: elapsedMs(start),
          errorType: c.error?.name,
          requestBytes: intOrUndefined(c.req.header("content-length")),
          responseBytes: intOrUndefined(c.res.headers.get("content-length")),
          requestInfo: () => {
            const headers: Record<string, string> = {};
            c.req.raw.headers.forEach((value, key) => {
              headers[key.toLowerCase()] = value;
            });
            return { method: c.req.method, path: c.req.path, headers, raw: c };
          },
          llm: usage.summary(),
        });
      } catch {
        // never reaches the app
      }
    }
  };
  return Object.assign(middleware, { client });
}
