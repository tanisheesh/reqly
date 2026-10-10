import { AsyncLocalStorage } from "node:async_hooks";

/** What a `consumer` function receives. Header names are lower-case. */
export interface RequestInfo {
  method: string;
  path: string;
  headers: Record<string, string | undefined>;
  /** The framework's own request object (Express req, Fastify request, Hono context). */
  raw: unknown;
}

export interface LlmSummary {
  model: string;
  inputTokens: number;
  outputTokens: number;
}

/** LLM tokens recorded while one request was served, per model. */
export class LlmUsage {
  private readonly byModel = new Map<string, [number, number]>();

  add(model: string, inputTokens: number, outputTokens: number): void {
    const totals = this.byModel.get(model) ?? [0, 0];
    totals[0] += inputTokens;
    totals[1] += outputTokens;
    this.byModel.set(model, totals);
  }

  /** The model with the most tokens, with every model's tokens summed. */
  summary(): LlmSummary | undefined {
    let model: string | undefined;
    let best = -1;
    let input = 0;
    let output = 0;
    for (const [name, [i, o]] of this.byModel) {
      input += i;
      output += o;
      if (i + o > best) {
        best = i + o;
        model = name;
      }
    }
    return model === undefined ? undefined : { model, inputTokens: input, outputTokens: output };
  }
}

export const requestStorage = new AsyncLocalStorage<LlmUsage>();

function count(value: unknown): number {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n) || n < 0) throw new RangeError("token counts must be non-negative numbers");
  return Math.floor(n);
}

/**
 * Attribute LLM token usage to the request being served. Call it after each
 * model call inside a handler; calls add up. Outside an instrumented request
 * it does nothing. Never throws.
 */
export function recordLlmUsage(model: string, inputTokens = 0, outputTokens = 0): void {
  try {
    const usage = requestStorage.getStore();
    if (!usage) return;
    usage.add(String(model).slice(0, 255), count(inputTokens), count(outputTokens));
  } catch (err) {
    console.warn("reqly: recordLlmUsage() failed:", err);
  }
}

type Usage = Record<string, unknown> | undefined;

/**
 * recordLlmUsage() from a provider response: OpenAI-style
 * (`usage.prompt_tokens` / `completion_tokens`; also Groq, Mistral, vLLM...),
 * OpenAI Responses API and Anthropic (`usage.input_tokens` / `output_tokens`).
 * Never throws.
 */
export function recordLlmResponse(response: unknown): void {
  try {
    if (!response || typeof response !== "object") return;
    const r = response as { model?: unknown; usage?: Usage };
    const usage = r.usage;
    if (!usage) return;
    let input = usage.prompt_tokens;
    let output = usage.completion_tokens;
    if (input === undefined && output === undefined) {
      input = usage.input_tokens;
      output = usage.output_tokens;
    }
    recordLlmUsage(typeof r.model === "string" ? r.model : "unknown", Number(input ?? 0), Number(output ?? 0));
  } catch (err) {
    console.warn("reqly: recordLlmResponse() failed:", err);
  }
}
