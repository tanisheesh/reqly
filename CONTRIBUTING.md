# Contributing to Reqly

Thanks for taking the time to contribute! Bug reports, fixes, docs improvements, new framework integrations and feature ideas are all welcome.

---

## Before you start

- **Found a bug?** Open a [bug report](../../issues/new?template=bug_report.yml). Search existing issues first.
- **Have an idea?** Open a [feature request](../../issues/new?template=feature_request.yml) before writing code, so we can agree on the approach.
- **Found a security issue?** Don't open a public issue. Follow [SECURITY.md](SECURITY.md).
- Small fixes (typos, docs, obvious bugs) can go straight to a pull request.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Local setup

Full instructions and every env var are in [docs/SETUP.md](docs/SETUP.md); how to use Reqly is on the [documentation site](https://reqly.tanisheesh.in/docs/). Short version:

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/reqly   # your fork
cd reqly
docker compose up -d        # TimescaleDB, collector, dashboard, demo traffic
```

Dashboard on http://localhost:5173, collector API docs on http://localhost:8000/docs. No `.env` needed; `cp .env.example .env` to override anything.

| Folder | What | Language |
|---|---|---|
| `sdk/` | Python SDK, `pip install reqly` | Python 3.9+ |
| `sdk-node/` | Node SDK, `npm install reqly-node` | TypeScript, Node 20+ |
| `collector/` | Ingest, OTLP, metrics API, alerts, SLOs, Ask Reqly, auth | Python, FastAPI |
| `collector/migrations/` | Schema, applied in order on start-up | SQL |
| `dashboard/` | Web UI | React 19, Vite, TypeScript |
| `bench/` | SDK overhead benchmarks | Python, Node |
| `landing/` | reqly.tanisheesh.in and the docs site (`landing/docs/`, served at `/docs/`) | HTML, MkDocs Material |
| `infra/` | EC2 bootstrap, optional AWS Lambda | shell, SAM |
| `deploy/helm/reqly/` | Helm chart (`helm lint` runs in CI) | Helm |

---

## Workflow

1. **Fork** the repo and create a branch from `main` (`main` is always deployable):

   | Branch prefix | Use for |
   |---|---|
   | `feat/` | new feature, e.g. `feat/koa-integration` |
   | `fix/` | bug fix, e.g. `fix/otlp-route-template` |
   | `docs/` | documentation only |
   | `chore/` | tooling, config, dependencies |
   | `refactor/` | code change with no behaviour change |

2. **Make your change.** Keep it focused, one feature or fix per PR, and add tests with it.
3. **Check it locally** before pushing. Run the checks for the parts you touched; CI runs all of them:

   ```bash
   # Python SDK
   cd sdk && pip install -e ".[dev]" && pytest

   # Node SDK
   cd sdk-node && npm ci && npm test

   # Collector (DB tests run when TimescaleDB is reachable)
   docker compose up timescaledb -d
   cd collector && pip install -e ".[dev]" && pytest

   # Dashboard: type-check and build
   cd dashboard && npm ci && npm run build
   ```

   Changing an SDK's request path? Run the benchmark too (`bench/README.md`). It also runs in CI on SDK changes.

4. **Open a pull request** against `main`. The PR template will guide you.

### New framework integrations

- **Python:** create `sdk/reqly/integrations/<framework>.py` with `instrument_<framework>(app, client: ReqlyClient)`. See `fastapi.py` and `flask.py` for the pattern. Record the route **template**, never the raw path.
- **Node:** add the middleware to `sdk-node/src/frameworks.ts` and export it from `index.ts`.
- Add tests, a row to the SDK README's compatibility table, and an entry in the SDK's `CHANGELOG.md`.

### Definition of done

A change is done when it runs in production (the live demo, or a released package), was tested against the real deployment and not only locally, and the docs say what changed. Releases are described in [docs/DEPLOY.md](docs/DEPLOY.md#6-releasing-packages-and-images).

---

## Commit messages

Commits follow an emoji-prefixed format:

```
<emoji> [#issue] [scope:] <message in present tense, lowercase>
```

| Emoji | Type | Example |
|---|---|---|
| ✨ | New feature | `✨ sdk: add consumer tracking` |
| 🐛 | Bug fix that affects users | `🐛 #42 collector: refuse events older than the raw retention` |
| 🎊 | Improvement | `🎊 sdk: halve the per-request overhead` |
| ♻️ | Refactor, no behaviour change | `♻️ extract scoring into its own module` |
| 🧪 | Tests only | `🧪 cover otlp spans without http.route` |
| 📚 | Docs only | `📚 update architecture for projects and keys` |
| 👷 | CI/CD | `👷 run the benchmark on sdk changes` |
| 🔨 | Build files, configs, chores | `🔨 ignore local PLAN.md` |
| 👆 | Dependency update | `👆 upgrade fastapi to 0.120` |
| 🔒 | Security | `🔒 cap request bodies at 16 MB` |
| 📁 | Database schema | `📁 add api_keys table` |
| 🚀 | Release | `🚀 reqly SDK 0.5.2` |
| 💀 | Code removal | `💀 remove unused legacy route` |
| 🎨 | Formatting only | `🎨 normalize log format across routers` |

Breaking changes add a line `🚨 BREAKING CHANGE: <what breaks>` in the commit body. One unit of logic per commit. No force-push or `git reset --hard` on `main`.

---

## Code style

- Follow the existing file structure and patterns. `.editorconfig` sets indentation (4 spaces for Python, 2 for TypeScript) and line endings.
- Python: snake_case, type hints on public functions. TypeScript: camelCase, strict mode.
- The SDKs must never raise into the host app, and must never capture request bodies, query strings or headers.
- No secrets in code. `.env*` stays gitignored; add new variables to `.env.example` (names only) and to `docs/SETUP.md`.
- Update docs when behaviour, setup or env vars change: in the same PR as the code they describe. User-facing docs live in `landing/docs/` (preview with `cd landing && pip install -r requirements-docs.txt && mkdocs serve`); design docs in `docs/`.

---

## Pull request checklist

- [ ] Branch is up to date with `main`
- [ ] Tests (and the dashboard build) pass locally for the parts you touched
- [ ] Commits follow the format above
- [ ] Docs / `.env.example` / `CHANGELOG.md` updated if needed
- [ ] PR description explains **what** and **why**

PRs are reviewed as soon as possible. Feedback is about the code, never the person.

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
