# Express → Reqly via OpenTelemetry

An Express app reporting to Reqly with **zero Reqly code** — just OpenTelemetry's Node
auto-instrumentation and environment variables. See the [OpenTelemetry guide](https://reqly.tanisheesh.in/docs/instrument/opentelemetry/).

```bash
# 1. Start Reqly (from the repo root)
docker compose up -d

# 2. Run the example
cd examples/otel/express
npm install

OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000/otlp \
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
OTEL_EXPORTER_OTLP_HEADERS=x-reqly-key=demo-key \
OTEL_SERVICE_NAME=express-example \
OTEL_RESOURCE_ATTRIBUTES=service.version=v1,deployment.environment.name=dev \
OTEL_METRICS_EXPORTER=none OTEL_LOGS_EXPORTER=none \
OTEL_SEMCONV_STABILITY_OPT_IN=http \
npm start

# 3. Send some traffic
for i in $(seq 1 50); do
  curl -s localhost:3000/users/$i > /dev/null
  curl -s -X POST localhost:3000/orders -H 'content-type: application/json' -d '{}' > /dev/null
done
```

Open the dashboard at http://localhost:5173 and pick **express-example**. You'll see
`/users/:id` and `/orders` with latency percentiles, the injected ~5% error rate on
`/orders`, and release `v1` on every event.

`OTEL_SEMCONV_STABILITY_OPT_IN=http` makes the Node instrumentation emit the stable HTTP
attribute names; Reqly also understands the legacy names, so it works without it too.

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
