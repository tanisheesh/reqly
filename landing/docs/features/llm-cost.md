---
title: LLM cost
description: Tokens and estimated cost per route, model and day for endpoints that call LLMs.
---

# LLM cost per route

If your API calls language models, record the token usage during the request and Reqly shows **tokens and estimated cost per route, model and day**, including cost per 1,000 requests. It tells you which endpoint is the expensive one.

## Record usage

=== "Python"

    ```python
    completion = client.chat.completions.create(model="gpt-4o-mini", messages=messages)
    reqly.record_llm_response(completion)   # OpenAI- or Anthropic-style responses, or:
    reqly.record_llm_usage("gpt-4o-mini", input_tokens=1200, output_tokens=240)
    ```

=== "Node.js"

    ```js
    import { recordLlmResponse, recordLlmUsage } from "reqly-node";

    const completion = await openai.chat.completions.create({ model: "gpt-4o-mini", messages });
    recordLlmResponse(completion);
    recordLlmUsage("gpt-4o-mini", 1200, 240);
    ```

Using OpenTelemetry instead of an SDK? LLM calls recorded as GenAI spans are picked up automatically: see [OpenTelemetry → LLM calls](../instrument/opentelemetry.md#llm-calls).

Call it anywhere while the request is being served; it's tied to the current request (a context variable in Python, `AsyncLocalStorage` in Node). If one request calls several models, all tokens are summed and the request is attributed to the model with the most tokens.

## Prices

Cost is computed **when you query it** from a price table, in USD per 1 million input and output tokens. Editing a price applies to past usage too.

The built-in table, [`llm_prices.yaml`](https://github.com/tanisheesh/reqly/blob/main/collector/app/llm/llm_prices.yaml), has list prices for OpenAI, Anthropic, Google and Groq models, checked against the providers' pricing pages in October 2026. Prices change and contracts differ, so check the ones you use. To override or add models, point `LLM_PRICES_FILE` at your own YAML file in the same format:

```yaml
models:
  gpt-4o-mini:   {input: 0.15, output: 0.60}
  my-finetune:   {input: 0.30, output: 1.20}
```

A model name matches a table entry when it is that entry or a dated snapshot of it, case-insensitive, after dropping a provider prefix: `openai/gpt-4o-2024-08-06` and `anthropic.claude-opus-5-5` match `gpt-4o` and `claude-opus-5-5`. A different model that merely starts the same way (`gpt-4o-mini`, `gemini-2.5-flash-lite`, a future `gpt-5.7`) needs its own entry. Models with no match are listed as **unpriced**, never guessed.

## Alerts

When a route's LLM spend jumps, an hourly alert tells you whether it was **cost per request** (prompt, model, output length) or **request volume**, and which factor moved. See [LLM cost alerts](alerts.md#llm-cost-alerts).

## API

```bash
# window=24h|7d|30d
curl "http://localhost:8000/v1/services/checkout-api/llm-usage?window=7d" -H "X-Reqly-Key: demo-read-key"
```

Usage is rolled up per hour, route and model, and kept for 90 days.
