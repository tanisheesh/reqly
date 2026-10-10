---
title: Projects, keys & sign-in
description: Share one Reqly collector between teams with projects, scoped API keys and dashboard users.
---

# Projects, keys & sign-in

A single collector can serve several teams. Each team gets a **project** with its own **API keys** and **members**, and only sees its own services.

## Who can do what

| Caller | Can | Reaches |
|---|---|---|
| `REQLY_INGEST_KEY` | ingest + admin | every project |
| `REQLY_READ_KEY` (only while `PUBLIC_DASHBOARD=true`) | read | every project |
| Project key `rqk_…` | its scopes: `ingest`, `read`, `admin` | its project |
| Admin user | read + admin | every project |
| Member user | read | the projects they're a member of |

A valid key used for something its scopes don't allow gets **403**; an unknown key gets **401**.

## Turn on sign-in

For a shared collector, turn the public dashboard off and create the first admin:

```bash
PUBLIC_DASHBOARD=false
REQLY_ADMIN_USERNAME=admin
REQLY_ADMIN_PASSWORD=at-least-12-characters
```

The admin is created at start-up when there are no users yet. More users, and password resets, from the collector's shell:

```bash
docker compose exec collector python -m app.users create-user alice           # add --admin for an admin
docker compose exec collector python -m app.users set-password alice
```

Passwords are hashed with argon2id. A sign-in is a bearer session (7 days by default, `SESSION_TTL_HOURS`); changing a password ends all of that user's sessions.

## Manage it from the dashboard

Signed-in admins have **Settings** in the header:

- **Projects:** create projects and move services between them
- **API keys:** create keys with scopes. A new key is shown once, with a copy button. Revoking asks you to confirm
- **Members:** add users to a project
- **Password:** change your own

With more than one project, a project switcher in the header narrows the service list.

## Or over the API

```bash
# a project
curl -X POST http://localhost:8000/v1/projects -H "X-Reqly-Key: demo-key" \
  -H "Content-Type: application/json" -d '{"slug": "payments", "name": "Payments team"}'

# an ingest key for it (returned once)
curl -X POST http://localhost:8000/v1/projects/2/keys -H "X-Reqly-Key: demo-key" \
  -H "Content-Type: application/json" -d '{"name": "checkout-api prod", "scopes": ["ingest"]}'
```

Use the `rqk_…` key as the SDK's `api_key`, or in the OTLP `x-reqly-key` header.

| Endpoint | Does |
|---|---|
| `GET /v1/projects` | Projects you can see, with their services |
| `POST /v1/projects` | Create a project |
| `PUT /v1/projects/{id}/services` | Move a service, and its data, to a project |
| `GET` / `POST /v1/projects/{id}/keys` | List or create keys |
| `POST /v1/keys/{id}/revoke` | Revoke a key |
| `GET` / `POST` / `DELETE /v1/projects/{id}/members[/{user_id}]` | Manage members |

## How services join a project

A new service joins the project of the key that sends its first events. After that, another project's keys get 403 for it (OTLP spans are rejected as a partial success). Services that had data before projects existed are in the `default` project. Move a service with `PUT /v1/projects/{id}/services`.

Keys are stored only as SHA-256 hashes; the dashboard shows a prefix to tell them apart. Revoking takes effect immediately on the collector that did it, and within 60 seconds elsewhere.
