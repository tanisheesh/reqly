# Deploying Reqly

> **Live:** [dashboard](https://reqly-eventflow-dashboard.onrender.com) · [collector](https://reqly-eventflow-collector.onrender.com/v1/health) · [website](https://reqly.tanisheesh.in) · **Hosts:** Render, AWS EC2, Vercel · **Auto-deploy:** on push to `main`

For local development, see [SETUP.md](SETUP.md). Command-by-command AWS steps (EC2 database, optional Lambda) are in [infra/DEPLOY.md](../infra/DEPLOY.md).

---

## 1. Architecture in production

| Piece | Runs on | Notes |
|---|---|---|
| Collector | Render web service | FastAPI on uvicorn, port 8000. Applies migrations on start-up. Run **one** instance: the scheduler, rate limits and caches are per process |
| Dashboard | Render | React SPA built with `VITE_COLLECTOR_URL` / `VITE_READ_KEY` |
| Database | AWS EC2 t3.micro | `timescale/timescaledb-ha:pg16` in Docker (includes the Toolkit), TLS with a self-signed certificate, set up by `infra/ec2-userdata.sh` |
| Weekly report (optional) | AWS Lambda + EventBridge + S3 | `infra/sam`. The demo runs the report inside the collector instead |
| Website | Vercel | Static `landing/index.html`. The project's root directory is `landing/`, so nothing else in the repo is served |
| Packages | PyPI, npm, GHCR | `reqly`, `reqly-node`, `ghcr.io/tanisheesh/reqly-collector`, `reqly-dashboard` (see section 6) |
| DNS | GoDaddy | `reqly.tanisheesh.in` → CNAME to Vercel |

---

## 2. Environment variables

Set these in the host dashboard (never commit them). Every variable, its default and where to get it is in [SETUP.md § 2](SETUP.md#2-environment-variables) and [`.env.example`](../.env.example).

**Collector**

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | ✅ | `postgresql://reqly:<password>@<host>:5432/reqly?sslmode=require` |
| `REQLY_INGEST_KEY` | ✅ | Write key for SDKs and admin API calls |
| `REQLY_READ_KEY` | ✅ | Read key compiled into the dashboard. Must differ from the ingest key |
| `CORS_ORIGINS` | ✅ | The dashboard's origin |
| `FORWARDED_ALLOW_IPS` | ✅ behind a proxy | `*` on Render, so rate limits see the real client IP |
| `GROQ_API_KEY` | ❌ | AI reports and Ask Reqly. Without it reports are plain text and Ask is off |
| `PUBLIC_DASHBOARD` | ❌ | `true` for the public demo; `false` requires sign-in |
| `REQLY_ADMIN_PASSWORD` | ❌ | Creates the first admin when there are no users (12+ characters) |
| `ALERT_SLACK_WEBHOOK_URL` / `ALERT_DISCORD_WEBHOOK_URL` / `ALERT_WEBHOOK_URL` | ❌ | Where alerts go |
| `INSIGHTS_SCHEDULER_ENABLED` | ❌ | `false` only if the SAM Lambda runs the weekly report |

**Dashboard:** `VITE_COLLECTOR_URL` and `VITE_READ_KEY` at build time, or `REQLY_COLLECTOR_URL` and `REQLY_READ_KEY` at run time with the prebuilt image.

**Website:** none.

> After changing an env var, **redeploy**. Running deployments don't pick up new values.

---

## 3. First-time deploy

### Database (EC2)

Follow [infra/DEPLOY.md § Step 1](../infra/DEPLOY.md#step-1--launch-ec2-timescaledb). The user-data script installs Docker, adds swap, starts TimescaleDB with TLS, and refuses to run with the placeholder password. Connect with `?sslmode=require`.

### Collector and dashboard on Render

1. [dashboard.render.com](https://dashboard.render.com) → New → **Web Service** → connect `tanisheesh/reqly`, root directory `collector`, runtime Docker (`collector/Dockerfile`), or deploy the image `ghcr.io/tanisheesh/reqly-collector:<version>`
2. Add the collector env vars from section 2, deploy, and check `GET /v1/health`
3. New → **Static Site** for `dashboard/`: build command `npm ci && npm run build`, publish directory `dist`, with `VITE_COLLECTOR_URL` and `VITE_READ_KEY` set
4. Add the dashboard's URL to the collector's `CORS_ORIGINS`

### Docker (self-host anywhere)

```bash
docker run -d -p 8000:8000 \
  -e DATABASE_URL="postgresql://reqly:...@db-host:5432/reqly?sslmode=require" \
  -e REQLY_INGEST_KEY=... -e REQLY_READ_KEY=... -e CORS_ORIGINS=https://reqly-dashboard.example.com \
  ghcr.io/tanisheesh/reqly-collector:0.10.0

docker run -d -p 5173:5173 \
  -e REQLY_COLLECTOR_URL=https://reqly.example.com -e REQLY_READ_KEY=... \
  ghcr.io/tanisheesh/reqly-dashboard:0.10.0
```

Images are built for linux/amd64 and arm64. Pin a version instead of `latest`.

### Website (Vercel)

1. [vercel.com/new](https://vercel.com/new) → import `tanisheesh/reqly`
2. Framework preset: **Other** · Root directory: `landing`
3. Deploy. `landing/vercel.json` sets static output, security headers, and skips builds for commits that don't touch `landing/`

---

## 4. Database migrations

Automatic. On start-up the collector runs every file in `collector/migrations/` that isn't recorded in its `schema_migrations` table yet, so deploying a new collector version is the migration. Migrations are forward-only.

---

## 5. Custom domain

`reqly.tanisheesh.in` is wired with the setup toolkit:

```powershell
.\scripts\setup-subdomain.ps1 -Subdomain "reqly" -Platform Vercel -VercelProject "reqly"
```

Manual fallback: add the domain in the Vercel project, then create a GoDaddy CNAME `reqly` → `cname.vercel-dns.com`. HTTPS certificates are issued automatically once DNS resolves (2–10 min). The collector and dashboard use their `onrender.com` addresses.

---

## 6. Releasing packages and images

Each part has its own tag; pushing the tag runs the workflow.

| Tag | Publishes | Workflow |
|---|---|---|
| `sdk-vX.Y.Z` | `reqly` to PyPI, plus a GitHub release | `release-sdk.yml` |
| `node-vX.Y.Z` | `reqly-node` to npm with provenance, plus a GitHub release; mirror `@tanisheesh/reqly-node` on GitHub Packages | `release-sdk-node.yml`, `github-packages.yml` |
| `collector-vX.Y.Z` | `ghcr.io/tanisheesh/reqly-collector` and `reqly-dashboard` (amd64 + arm64; tagged with the version, the commit SHA and `latest`) | `github-packages.yml` |

**Python SDK:** bump `version` in `sdk/pyproject.toml`, add a `## X.Y.Z — YYYY-MM-DD` section to `sdk/CHANGELOG.md`, merge to `main`, then:

```bash
git tag sdk-vX.Y.Z && git push origin sdk-vX.Y.Z
```

The workflow refuses to publish if the tag doesn't match `pyproject.toml`, the changelog has no entry for the version, or the version is already on PyPI. It runs the SDK tests, builds and checks the package, publishes it, then creates a GitHub release with the changelog section as notes. Start it by hand from the Actions tab to dry-run everything except publishing.

**Node SDK:** the same with `sdk-node/package.json`, `sdk-node/CHANGELOG.md` and a `node-vX.Y.Z` tag.

**Images:** bump `collector/pyproject.toml`, merge, tag `collector-vX.Y.Z`. Run `github-packages.yml` by hand to publish the current `main`.

No tokens are stored for any of this. PyPI and npm use trusted publishing (GitHub proves its identity with a short-lived OIDC token), and GHCR uses the workflow's own token.

**One-time setup (already done for this repo):**
- **PyPI:** a `pypi` environment in the repo settings, restricted to `sdk-v*` tags. A trusted publisher on [pypi.org → reqly → Publishing](https://pypi.org/manage/project/reqly/settings/publishing/) with owner `tanisheesh`, repository `reqly`, workflow `release-sdk.yml`, environment `pypi`.
- **npm:** npm only lets a package that already exists add a trusted publisher, so 0.1.0 was published by hand (`cd sdk-node && npm login && npm publish --access public`). Then npmjs.com → reqly-node → Settings → Trusted publishing → GitHub Actions with owner `tanisheesh`, repository `reqly`, workflow `release-sdk-node.yml`.

Add the release to the root [CHANGELOG.md](../CHANGELOG.md) as well.

---

## 7. Verify a deploy

- [ ] `GET https://reqly-eventflow-collector.onrender.com/v1/health` returns 200
- [ ] The dashboard loads, lists services and shows charts for the last hour
- [ ] Making traffic on [EventFlow](https://eventflow-g2h5.onrender.com) shows up on the dashboard within a minute or two
- [ ] With `GROQ_API_KEY` set, Ask Reqly answers a question
- [ ] [reqly.tanisheesh.in](https://reqly.tanisheesh.in) loads with no console errors

---

## 8. Rollback

| Host | How |
|---|---|
| Render | Service → Events → last good deploy → **Rollback** |
| Vercel | Dashboard → Deployments → pick the last good one → **Promote to Production** (or `vercel rollback`) |
| Images | Run the previous version tag (`ghcr.io/tanisheesh/reqly-collector:<previous>`) |
| PyPI / npm | Versions can't be replaced. Yank (PyPI) or deprecate (npm) the bad one and release a fix |
| Database | Migrations are forward-only. An older collector keeps working on a newer schema only when the change was additive, so restore from a backup of the EC2 volume if it wasn't |

After rolling back, note it in a commit or PR so the next push doesn't silently re-ship the broken version.

---

## 9. Monitoring & logs

- **Logs:** Render → service → Logs (collector, dashboard). EC2: `docker logs` on the TimescaleDB container
- **Errors:** none beyond the logs yet
- **Uptime:** none yet. Point a checker at `/v1/health`
- **Reqly on Reqly:** the demo's own traffic shows up in the dashboard, so a quiet dashboard is the first sign something is off
- **Dependencies:** Dependabot opens weekly update PRs, they are merged automatically once CI passes, and a maintenance report is emailed every Monday (see `.github/workflows/`)

---

## Known production limitations

- One collector instance only: the scheduler, rate limits and caches are per process
- The most recent ~1 minute of data isn't in the aggregates yet (continuous aggregate `end_offset`)
- With `PUBLIC_DASHBOARD` on, anyone with the dashboard URL can read every project. Meant for demos only
- AI features depend on Groq. Models get retired, so the model is configurable and reports fall back to plain text

---

<div align="center">

<h3>Tanish Poddar</h3>

<a href="https://tanisheesh.in"><img src="https://img.shields.io/badge/Website-tanisheesh.in-111111?style=flat-square&logo=googlechrome&logoColor=white" alt="Website"></a>
<a href="https://linkedin.com/in/tanisheesh"><img src="https://img.shields.io/badge/LinkedIn-tanisheesh-0A66C2?style=flat-square" alt="LinkedIn"></a>
<a href="https://github.com/tanisheesh"><img src="https://img.shields.io/badge/GitHub-tanisheesh-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub"></a>
<a href="mailto:hey@tanisheesh.in"><img src="https://img.shields.io/badge/Email-hey%40tanisheesh.in-EA4335?style=flat-square&logo=gmail&logoColor=white" alt="Email"></a>

</div>
