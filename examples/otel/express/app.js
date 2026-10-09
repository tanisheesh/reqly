// A plain Express app with no Reqly code at all. OpenTelemetry's Node
// auto-instrumentation (loaded via --require in package.json) traces every
// request and exports it to the Reqly collector's OTLP endpoint.
const express = require("express");

const app = express();
const users = express.Router();

users.get("/:id", (req, res) => res.json({ id: req.params.id }));
app.use("/users", users); // reported as route "/users/:id"

app.post("/orders", express.json(), (req, res) => {
  // ~5% of orders fail, so the error-rate chart has something to show
  if (Math.random() < 0.05) return res.status(503).json({ error: "inventory unavailable" });
  res.status(201).json({ ok: true });
});

const port = Number(process.env.PORT || 3000);
app.listen(port, () => console.log(`listening on http://localhost:${port}`));
