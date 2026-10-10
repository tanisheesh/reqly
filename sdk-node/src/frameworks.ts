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
  return expressMiddleware(toClient(clientOrOptions));
}

function expressMiddleware(client: ReqlyClient, isUnmatched?: (route: string, statusCode: number) => boolean) {
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
        let route = matched ?? (typeof routePath === "string" ? (req.baseUrl ?? "") + routePath : undefined);
        if (route !== undefined && isUnmatched?.(route, res.statusCode)) route = undefined;
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

  // Like the other integrations, only an error that becomes a 5xx is an
  // error: next(createError(404)) or a thrown 400 is the app answering.
  const errorHandler = (err: unknown, _req: ExpressRequest, res: ExpressResponse, next: Next): void => {
    if (res.locals && httpStatusOf(err) >= 500) res.locals[EXPRESS_ERROR] = (err as Error | undefined)?.name ?? "Error";
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

// --- Koa -----------------------------------------------------------------------

interface KoaContextLike {
  method: string;
  path: string;
  status: number;
  headers: Record<string, unknown>;
  req: object;
  response: { length?: number };
  _matchedRoute?: unknown; // set by @koa/router: the full template, prefixes included
}

function httpStatusOf(err: unknown): number {
  const e = err as { status?: unknown; statusCode?: unknown } | undefined;
  const status = Number(e?.status ?? e?.statusCode);
  return Number.isInteger(status) && status >= 400 && status <= 599 ? status : 500;
}

/**
 * Koa middleware (2 and 3). Register it first, before your routers:
 *
 *     app.use(reqlyKoa({ serviceName: "checkout-api" }));
 *     app.use(router.routes());
 *
 * Routes are @koa/router templates ("/api/users/:id", with router prefixes);
 * requests no route matched are "__unmatched__". A thrown error is recorded
 * with its status (500 unless it carries one, like ctx.throw(404)) and, for
 * 5xx, its type -- then rethrown for Koa to handle.
 */
export function reqlyKoa(clientOrOptions?: ClientOrOptions) {
  const client = toClient(clientOrOptions);

  const middleware = async (ctx: KoaContextLike, next: () => Promise<unknown>): Promise<void> => {
    if (!claim(ctx.req)) {
      await next();
      return;
    }
    const start = process.hrtime.bigint();
    const usage = new LlmUsage();
    let statusCode: number | undefined;
    let errorType: string | undefined;
    try {
      await requestStorage.run(usage, () => next());
    } catch (err) {
      // Koa sets the status only after the error leaves every middleware.
      statusCode = httpStatusOf(err);
      if (statusCode >= 500) errorType = (err as Error | undefined)?.name ?? "Error";
      throw err;
    } finally {
      try {
        const route = ctx._matchedRoute;
        client.record({
          method: ctx.method,
          route: typeof route === "string" ? route : undefined,
          statusCode: statusCode ?? ctx.status,
          durationMs: elapsedMs(start),
          errorType,
          requestBytes: intOrUndefined(ctx.headers["content-length"]),
          responseBytes: intOrUndefined(ctx.response.length),
          requestInfo: () => ({ method: ctx.method, path: ctx.path, headers: lowerHeaders(ctx.headers), raw: ctx }),
          llm: usage.summary(),
        });
      } catch {
        // never reaches the app
      }
    }
  };
  return Object.assign(middleware, { client });
}

// --- NestJS --------------------------------------------------------------------

/* Structural types so @nestjs/* and rxjs aren't dependencies. */
interface ObservableLike {
  constructor: new (subscribe: (subscriber: ObserverLike) => () => void) => ObservableLike;
  subscribe(observer: ObserverLike): { unsubscribe(): void };
  pipe(operator: (source: ObservableLike) => ObservableLike): ObservableLike;
}
interface ObserverLike {
  next(value: unknown): void;
  error(err: unknown): void;
  complete(): void;
}
interface NestExecutionContextLike {
  getType(): string;
  switchToHttp(): { getRequest(): unknown; getResponse(): unknown };
}
interface NestAppLike {
  getHttpAdapter(): { getType?(): string; getInstance(): unknown };
  use(...args: unknown[]): unknown;
  useGlobalInterceptors(...interceptors: unknown[]): unknown;
}

/** Nest's own 404 handler is a catch-all route ("*path", "/api*path"). */
function isNestNotFound(route: string, statusCode: number): boolean {
  return statusCode === 404 && /\*[A-Za-z_]*$/.test(route);
}

/** HttpException (and subclasses) carry their status; anything else is a 500. */
function nestErrorType(err: unknown): string | undefined {
  const e = err as { getStatus?: () => unknown; name?: string } | undefined;
  const status = typeof e?.getStatus === "function" ? Number(e.getStatus()) : 500;
  return status >= 500 ? (e?.name ?? "Error") : undefined;
}

/**
 * NestJS on the Express (default) or Fastify adapter. Call it before
 * `app.listen()`:
 *
 *     const app = await NestFactory.create(AppModule);
 *     reqlyNest(app, { serviceName: "checkout-api" });
 *     await app.listen(3000);
 *
 * Routes are the full templates ("/api/users/:id", with the global prefix and
 * controller path); Nest's catch-all 404 is "__unmatched__". A global
 * interceptor records the type of exceptions that become 5xx -- HttpExceptions
 * below 500 (NotFoundException, BadRequestException, ...) are not errors.
 */
export function reqlyNest(app: NestAppLike, clientOrOptions?: ClientOrOptions) {
  const client = toClient(clientOrOptions);
  const adapter = app.getHttpAdapter();
  const fastify = adapter.getType?.() === "fastify";

  if (fastify) {
    const plugin = reqlyFastify(client);
    (adapter.getInstance() as { register(plugin: unknown): unknown }).register(plugin);
  } else {
    app.use(expressMiddleware(client, isNestNotFound));
  }

  const interceptor = {
    intercept(context: NestExecutionContextLike, next: { handle(): ObservableLike }): ObservableLike {
      const stream = next.handle();
      if (context.getType() !== "http") return stream;
      const http = context.switchToHttp();
      const mark = (err: unknown) => {
        try {
          const type = nestErrorType(err);
          if (type === undefined) return;
          if (fastify) {
            (http.getRequest() as Record<symbol, unknown>)[ERROR] = type;
          } else {
            const res = http.getResponse() as ExpressResponse;
            if (res.locals) res.locals[EXPRESS_ERROR] = type;
          }
        } catch {
          // never reaches the app
        }
      };
      // An rxjs operator built from the stream's own Observable class, so
      // rxjs doesn't have to be a dependency.
      return stream.pipe(
        (source) =>
          new source.constructor((subscriber) => {
            const subscription = source.subscribe({
              next: (value) => subscriber.next(value),
              error: (err) => {
                mark(err);
                subscriber.error(err);
              },
              complete: () => subscriber.complete(),
            });
            return () => subscription.unsubscribe();
          }),
      );
    },
  };
  app.useGlobalInterceptors(interceptor);
  return { client };
}

// --- Plain node:http (any framework) -------------------------------------------

interface NodeRequestLike {
  method?: string;
  url?: string;
  headers: Record<string, unknown>;
}
interface NodeResponseLike {
  statusCode: number;
  getHeader(name: string): unknown;
  once(event: "finish" | "close", listener: () => void): unknown;
}

export interface ReqlyHttpOptions<Req = NodeRequestLike> extends ReqlyOptions {
  /**
   * The route template for a request ("/users/:id"), read when the response
   * finishes, so values your router set on the request by then are there.
   * Without one, every request is recorded as "__unmatched__" -- never as the
   * raw path.
   */
  routeResolver?: (req: Req) => string | null | undefined;
  /** A client to share with other middleware, instead of creating one. */
  client?: ReqlyClient;
}

/**
 * Wraps a plain `(req, res)` handler -- node:http, or a framework without a
 * built-in integration -- the way `instrument_wsgi` does for Python:
 *
 *     const handler = reqlyHttp(app, {
 *       serviceName: "checkout-api",
 *       routeResolver: (req) => req.matchedRoute,
 *     });
 *     http.createServer(handler).listen(3000);
 *
 * An error the handler throws (or a promise it returns rejects with) is
 * recorded with its type and rethrown.
 */
export function reqlyHttp<Req extends NodeRequestLike, Res extends NodeResponseLike>(
  handler: (req: Req, res: Res) => unknown,
  options: ReqlyHttpOptions<Req> = {},
) {
  const { routeResolver, client: shared, ...clientOptions } = options;
  const client = shared ?? new ReqlyClient(clientOptions);

  const wrapped = (req: Req, res: Res): unknown => {
    if (!claim(req)) return handler(req, res);
    const start = process.hrtime.bigint();
    const usage = new LlmUsage();
    let errorType: string | undefined;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      try {
        let route: string | undefined;
        try {
          const resolved = routeResolver?.(req);
          route = typeof resolved === "string" && resolved !== "" ? resolved : undefined;
        } catch {
          route = undefined; // a failing resolver means "no route", never an app error
        }
        const method = (req.method ?? "GET").toUpperCase();
        client.record({
          method,
          route,
          statusCode: res.statusCode,
          durationMs: elapsedMs(start),
          errorType,
          requestBytes: intOrUndefined(req.headers["content-length"]),
          responseBytes: intOrUndefined(res.getHeader("content-length")),
          requestInfo: () => ({
            method,
            path: (req.url ?? "/").split("?")[0],
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
    const markError = (err: unknown) => {
      errorType = (err as Error | undefined)?.name ?? "Error";
    };
    try {
      const out = requestStorage.run(usage, () => handler(req, res));
      if (out && typeof (out as Promise<unknown>).then === "function") {
        return (out as Promise<unknown>).then(undefined, (err: unknown) => {
          markError(err);
          throw err;
        });
      }
      return out;
    } catch (err) {
      markError(err);
      throw err;
    }
  };
  return Object.assign(wrapped, { client });
}
