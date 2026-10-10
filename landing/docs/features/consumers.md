---
title: API consumers
description: See who calls your API, who an incident hit, and who still uses deprecated endpoints.
---

# API consumers

Tag each request with the client that made it, and Reqly answers three questions:

1. **Who uses the API?** Top consumers by requests, with their share of traffic, error rate, p95 and routes
2. **Who did an incident hit?** Every [alert](alerts.md) lists how many of the consumers active on the route got errors, and the top ones
3. **Who still calls deprecated operations?** Shown in the [OpenAPI drift](openapi-drift.md) report

## Turn it on

=== "Python"

    ```python
    reqly.instrument(app, consumer_header="X-API-Key", consumer_salt=os.environ["REQLY_CONSUMER_SALT"])

    # or any logic:
    reqly.instrument(app, consumer=lambda info: info.headers.get("x-tenant-id"))
    ```

=== "Node.js"

    ```js
    reqlyExpress({ consumerHeader: "x-api-key", consumerSalt: process.env.REQLY_CONSUMER_SALT });

    // or any logic:
    reqlyExpress({ consumer: (info) => info.headers["x-tenant-id"] });
    ```

Or with environment variables: `REQLY_CONSUMER_HEADER` and `REQLY_CONSUMER_SALT`.

## Privacy

Consumer ids are **hashed with HMAC-SHA256 and your salt inside the SDK**, before they leave the process. The collector never sees API keys or user ids. The same id always hashes to the same value for a given salt, so a consumer is recognisable across requests without being reversible.

Set `consumer_salt` to a secret and keep it stable; changing it makes every consumer look new. For ids that aren't secret, such as tenant names, `hash_consumer=False` (`hashConsumer: false` in Node) sends them as they are, so they're readable on the dashboard.

## A cap on distinct consumers

The consumer id comes from your app, so a misconfiguration (a request id or a timestamp passed as the consumer) would create a new "consumer" on every request. To keep that from flooding the data, each service keeps the **first 1,000 distinct consumers per UTC day** as they are; consumers that appear after that are recorded as `__other__`, and they show up as one row of that name. Consumers already seen that day keep their own id.

Change the limit with `CONSUMER_LIMIT_PER_DAY` (`0` turns the cap off). It's per collector process and approximate at the edge: two batches arriving at the same moment can both take the last free place.

## Where the data comes from

Consumer ids have unbounded cardinality, so they stay out of the per-minute aggregates. Windows up to 7 days read the raw events; 30 days read an hourly rollup per service, consumer, route and method.

## API

```bash
# top consumers: window=24h|7d|30d
curl "http://localhost:8000/v1/services/checkout-api/consumers?window=7d" -H "X-Reqly-Key: demo-read-key"

# one consumer's routes and daily requests
curl "http://localhost:8000/v1/services/checkout-api/consumers/<consumer id>" -H "X-Reqly-Key: demo-read-key"
```
