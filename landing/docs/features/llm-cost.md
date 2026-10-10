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

Call it anywhere while the request is being served; it's tied to the current request (a context variable in Python, `AsyncLocalStorage` in Node). If one request calls several models, all tokens are summed and the request is attributed to the model with the most tokens.

## Prices

Cost is computed **when you query it** from a price table, in USD per 1 million input and output tokens. Editing a price applies to past usage too.

The built-in table, [`llm_prices.yaml`](https://github.com/tanisheesh/reqly/blob/main/collector/app/llm/llm_prices.yaml), has list prices for OpenAI, Anthropic and Google models as published in October 2025. Prices change and contracts differ, so check them before relying on the numbers. To override or add models, point `LLM_PRICES_FILE` at your own YAML file in the same format:

```yaml
models:
  gpt-4o-mini:   {input: 0.15, output: 0.60}
  my-finetune:   {input: 0.30, output: 1.20}
```

Model names match by the longest prefix, case-insensitive, after dropping a provider prefix: `openai/gpt-4o-2024-08-06` matches `gpt-4o`. Models with no match are listed as **unpriced**, never guessed.

## API

```bash
# window=24h|7d|30d
curl "http://localhost:8000/v1/services/checkout-api/llm-usage?window=7d" -H "X-Reqly-Key: demo-read-key"
```

Usage is rolled up per hour, route and model, and kept for 90 days.
