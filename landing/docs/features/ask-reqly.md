---
title: Ask Reqly
description: Ask questions about a service in plain English and get answers you can verify.
---

# Ask Reqly

Ask a question about one service in plain English, for example:

- *"Why did /orders start failing on Friday?"*
- *"Was checkout slower yesterday afternoon than last week?"*
- *"Which clients still call deprecated endpoints?"*
- *"What did the LLM calls on /summarize cost this week?"*
- *"Why did our LLM bill jump today?"*

The answer comes from your own data, and the dashboard shows every query the model ran next to it.

## Not text-to-SQL

The read key ships in the dashboard, so letting a model write SQL would turn it into a public SQL console. Instead, the model can only call **nine read-only tools**. Their arguments are validated and their time ranges are clamped to retention:

| Tool | Returns |
|---|---|
| `get_stats` | Requests, errors, p50/p95/p99, in total or per route, hour or day |
| `compare_periods` | The same stats for two periods side by side, optionally per route |
| `get_breakdown` | Traffic and errors by host, environment, release, status code, error type, method or consumer |
| `list_releases` | Releases with first/last seen, volume, error rate and p95 |
| `get_alerts` | Open and recent alerts: anomalies, SLO burn and LLM cost spikes |
| `get_slos` | SLOs with SLI, budget left and burn rates |
| `get_api_drift` | Undocumented, unused and deprecated-but-called endpoints |
| `get_consumers` | Top API consumers |
| `get_llm_costs` | LLM tokens and cost per route and model |

Results are rounded aggregates, never raw events. The model gets the current UTC time, the dates of the last 8 days, the service's routes and its recent releases up front, so "last Monday" or "since the deploy" resolve without a lookup.

## Checked numbers

Models occasionally mis-copy a digit. After the answer is written, every number in it is looked up in the tool results: as written, as a percentage, ms as seconds, a ratio as a percent change. Numbers that can't be found come back as `unverified_numbers`, and the dashboard flags them.

## Limits

- At most **6 tool calls** per question. After that the model must answer from what it has.
- **5 questions per minute** per IP, and `ASK_DAILY_LIMIT` per collector per day (default 200; `0` turns Ask off). The read key is public to dashboard viewers, and every question is an LLM call.
- Needs `GROQ_API_KEY`. Without it the endpoint returns 503. The model is `ASK_MODEL`, which defaults to `GROQ_MODEL` (`openai/gpt-oss-120b`) and must support tool calling.

## API

```bash
curl -X POST http://localhost:8000/v1/ask \
  -H "X-Reqly-Key: demo-read-key" -H "Content-Type: application/json" \
  -d '{"service_name": "checkout-api", "question": "Why did /orders start failing on Friday?"}'
```

The response contains the answer, every tool call with its arguments and result, and `unverified_numbers`.

An eval set of 19 questions over the demo scenarios lives in [`collector/tests/ask_eval/`](https://github.com/tanisheesh/reqly/tree/main/collector/tests/ask_eval). It's run by hand after prompt or model changes and needs a Groq key.
