# Security Policy

## Supported versions

Only the latest release of each part receives security fixes:

| Part | Supported |
|---|---|
| Collector and dashboard on `main` (and the live demo) | ✅ |
| `reqly` on PyPI, latest version | ✅ |
| `reqly-node` on npm, latest version | ✅ |
| Older releases | ❌ |

---

## Reporting a vulnerability

**Please don't open a public issue for security problems.**

Report privately through either channel:

1. **GitHub:** go to the [Security tab → Report a vulnerability](../../security/advisories/new) (preferred)
2. **Email:** [hey@tanisheesh.in](mailto:hey@tanisheesh.in) with the subject `Security: Reqly`

Please include:

- What the issue is and where it lives (endpoint, file, SDK and version)
- Steps to reproduce, or a minimal proof of concept
- The impact you think it has
- Any suggested fix (optional)

---

## What to expect

| Step | Timeline |
|---|---|
| Acknowledgement of your report | within **72 hours** |
| Initial assessment and severity | within **7 days** |
| Fix released for confirmed issues | as soon as possible, based on severity |

You'll be kept updated throughout, and credited in the release notes once it's fixed (unless you'd rather stay anonymous).

---

## Scope

**In scope**

- This repository's source code: the collector, the dashboard, the Python SDK (`sdk/`) and the Node SDK (`sdk-node/`)
- The live demo: [reqly-eventflow-collector.onrender.com](https://reqly-eventflow-collector.onrender.com), [reqly-eventflow-dashboard.onrender.com](https://reqly-eventflow-dashboard.onrender.com) and [reqly.tanisheesh.in](https://reqly.tanisheesh.in)
- Authentication and access control: ingest and read keys, project keys and their scopes, dashboard sign-in and sessions, one project's keys reaching another project's services
- Ingest and OTLP validation, request size and event age limits, rate limiting
- Ask Reqly reaching data or actions outside its read-only query tools
- An SDK raising into the host app, or sending data it shouldn't (request bodies, query strings, headers, unhashed consumer ids)
- The published images (`ghcr.io/tanisheesh/reqly-collector`, `reqly-dashboard`) and packages

**Out of scope**

- The read key being readable in the dashboard's JavaScript while `PUBLIC_DASHBOARD` is on. That's by design for public demos (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#7-security)); report it only if it reaches something beyond reads.
- The EventFlow demo app. It lives in its own repository.
- Vulnerabilities in third-party services (Render, Vercel, AWS, Groq, GitHub). Report those to the vendor.
- Self-hosted deployments running with the default keys (`demo-key` / `demo-read-key`, which the collector warns about at start-up) or with `CORS_ORIGINS=*`.
- Denial-of-service, spam or volumetric attacks
- Social engineering
- Findings from automated scanners with no demonstrated impact

Please act in good faith: don't access or modify data that isn't yours, and don't degrade the live demo while testing.

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
