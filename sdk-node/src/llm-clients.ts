import { LlmUsage, requestStorage, usageFromResponse } from "./context.js";

/**
 * Token usage from LLM clients without recordLlmUsage() calls:
 *
 *   const openai = instrumentOpenAI(new OpenAI());
 *   const anthropic = instrumentAnthropic(new Anthropic());
 *
 * Patches that one client (never the classes): its create methods -- OpenAI
 * chat completions, Responses, completions and embeddings; Anthropic messages
 * -- add each call's tokens to the request that made it. Streams count when
 * they finish; OpenAI chat streams only report usage with
 * `stream_options: { include_usage: true }`. Works with OpenAI-compatible
 * clients shaped the same way. Never throws into the app, and returns the
 * provider's own promise, so `.withResponse()` and friends keep working.
 */

const OPENAI_METHODS = [["chat", "completions"], ["responses"], ["completions"], ["embeddings"]];
const ANTHROPIC_METHODS = [["messages"]];
const PATCHED = Symbol.for("reqly.instrumented");

type Chunk = Record<string, any>;

function add(usage: LlmUsage | undefined, found: [string, number, number] | undefined): void {
  if (usage && found) usage.add(found[0].slice(0, 255), found[1], found[2]);
}

/** Usage seen in a stream's chunks (OpenAI chat, Responses, Anthropic events). */
class StreamTally {
  model: string | undefined;
  input = 0;
  output = 0;
  seen = false;

  observe(chunk: Chunk): void {
    if (!chunk || typeof chunk !== "object") return;
    if (chunk.type === "message_start") {
      this.model = chunk.message?.model ?? this.model;
      this.input = Number(chunk.message?.usage?.input_tokens ?? 0);
      this.output = Number(chunk.message?.usage?.output_tokens ?? 0);
      this.seen = true;
    } else if (chunk.type === "message_delta") {
      if (chunk.usage?.output_tokens !== undefined) {
        this.output = Number(chunk.usage.output_tokens);
        this.seen = true;
      }
    } else if (chunk.type === "response.completed") {
      this.take(usageFromResponse(chunk.response));
    } else if (chunk.usage) {
      this.take(usageFromResponse(chunk));
    }
    if (this.model === undefined && typeof chunk.model === "string") this.model = chunk.model;
  }

  private take(found: [string, number, number] | undefined): void {
    if (!found) return;
    [this.model, this.input, this.output] = found;
    this.seen = true;
  }

  result(): [string, number, number] | undefined {
    return this.seen ? [this.model ?? "unknown", this.input, this.output] : undefined;
  }
}

function watchStream(stream: any, usage: LlmUsage | undefined): void {
  const iterate = stream[Symbol.asyncIterator]?.bind(stream);
  if (!iterate) return;
  stream[Symbol.asyncIterator] = async function* () {
    const tally = new StreamTally();
    try {
      for await (const chunk of { [Symbol.asyncIterator]: iterate }) {
        try {
          tally.observe(chunk);
        } catch {
          // an unreadable chunk costs its usage, not the stream
        }
        yield chunk;
      }
    } finally {
      add(usage, tally.result());
    }
  };
}

function wrap(create: (...args: any[]) => any): (...args: any[]) => any {
  return function (this: unknown, ...args: any[]) {
    const usage = requestStorage.getStore(); // the request that made the call
    const result = create.apply(this, args);
    if (usage && result && typeof result.then === "function") {
      const streaming = Boolean(args[0]?.stream);
      result.then(
        (value: unknown) => {
          try {
            if (streaming) watchStream(value, usage);
            else add(usage, usageFromResponse(value));
          } catch {
            // never into the app
          }
        },
        () => {}, // the caller sees the rejection on its own promise
      );
    }
    return result;
  };
}

function instrument<T>(client: T, paths: string[][]): T {
  const target = client as any;
  if (!target || target[PATCHED]) return client;
  let patched = 0;
  for (const path of paths) {
    let resource = target;
    for (const name of path) resource = resource?.[name];
    if (!resource || typeof resource.create !== "function") continue;
    resource.create = wrap(resource.create.bind(resource));
    patched += 1;
  }
  if (patched === 0) console.warn("reqly: this client has none of the create methods to instrument");
  else target[PATCHED] = true;
  return client;
}

/** Count the tokens of every call made with this OpenAI (or OpenAI-compatible) client. */
export function instrumentOpenAI<T>(client: T): T {
  return instrument(client, OPENAI_METHODS);
}

/** Count the tokens of every call made with this Anthropic client. */
export function instrumentAnthropic<T>(client: T): T {
  return instrument(client, ANTHROPIC_METHODS);
}
