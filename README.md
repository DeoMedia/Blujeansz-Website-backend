# Blujeansz Website — Backend

API, database, security and storage for the
[BLUJEANSZ website](https://github.com/DeoMedia/Blujeansz-Website) CMS.

A FastAPI service owns all database access. Supabase provides Postgres, Auth and
Storage underneath it: Supabase Auth issues the tokens, and this service
verifies them and maps them onto a CMS profile. The React app talks only to this
API — it does not query Postgres directly.

Row Level Security is still applied to every table as defence in depth. The API
is the enforcement point in practice, but a leaked anon key alone still cannot
read a draft.

## Layout

```
app/
  main.py         App factory, CORS, error handling, health checks
  models.py       SQLAlchemy models mirroring the migrations
  schemas.py      Request/response models; content blocks as a validated union
  core/           Config, database engine, auth, slug generation
  routers/        insights, case_studies, directory, media, dashboard
supabase/
  migrations/     Ordered SQL migrations — the source of truth for the schema
  tests/          Local validation harness (not deployed)
docs/
  content-blocks.md   The article content JSON format
```

## Running locally

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Scripts/ -> bin/ on macOS and Linux
cp .env.example .env                                      # then fill in
.venv/Scripts/uvicorn app.main:app --reload
```

Interactive API docs are at `/docs` (disabled when `ENVIRONMENT=production`).

Dependencies are pinned to versions that publish wheels for Python 3.13/3.14.
Older pins of `pydantic-core`, `asyncpg` and `greenlet` have no 3.14 wheels and
fall back to a Rust/C source build, which fails without a local toolchain.

## Deploying to Railway

Point a service at this repo. `Procfile` provides the start command and
`.python-version` pins the interpreter. Set these service variables:

| Variable | |
|---|---|
| `DATABASE_URL` | Supabase **session** pooler URI (port 5432) |
| `SUPABASE_URL` | Project URL |
| `SUPABASE_ANON_KEY` | Public anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side only — required for media uploads |
| `CORS_ORIGINS` | Comma-separated; include the production domain |
| `ENVIRONMENT` | `production` |

Use the session pooler, not the transaction pooler: asyncpg's prepared
statements are incompatible with pgbouncer in transaction mode.

`/health` is a liveness probe and does not touch the database. `/health/ready`
does, and is the one to use for readiness.

## Applying migrations

Never create tables by hand in the Supabase dashboard — the migrations are the
source of truth and are written to be re-runnable.

With the [Supabase CLI](https://supabase.com/docs/guides/cli):

```bash
supabase link --project-ref <your-project-ref>
supabase db push
```

Or paste each file in `supabase/migrations/` into the dashboard SQL editor **in
filename order**.

| Migration | What it does |
|---|---|
| `20260808000001_initial_schema.sql` | Enums, 11 tables, indexes, FKs, constraints, `updated_at` triggers, profile provisioning trigger. Enables RLS on every table. |
| `20260808000002_rls_policies.sql` | Identity helpers and the full policy set. |
| `20260808000003_storage.sql` | The `media` bucket and its object policies. |
| `20260808000004_publishing.sql` | `publish_due_content()` plus a pg_cron schedule for scheduled posts. |
| `20260808000005_seed_reference_data.sql` | Categories, services, site settings, current team. |

## Permission model

Roles are ranked `super_admin > admin > editor > author`.

| | Insights | Case studies | Authors / Staff | Media | Users |
|---|---|---|---|---|---|
| **anon** | read published | read published | read active | read | — |
| **author** | create; edit own | — | read | upload own | — |
| **editor** | edit all | create + edit | read | upload own | — |
| **admin** | full + publish | full + publish | full | full | manage |
| **super_admin** | full | full | full | full | full, incl. super admins |

Two rules are worth calling out because they are enforced in SQL, not in React:

- **Publishing is a separate privilege.** Moving a row to `published` or
  `scheduled` requires `auth_can_publish()` — true for admins, and for editors
  and authors only when `profiles.can_publish` has been granted.
- **Nobody can escalate their own privileges.** A trigger on `profiles` rejects
  any self-change to `role`, `status` or `can_publish`, and only a super admin
  can mint another super admin.

Anonymous visibility is defined once, in `is_publicly_visible()`:
`status = 'published' AND published_at <= now()`. Drafts and scheduled content
are unreachable with the anon key regardless of what the frontend requests.

## Validating changes locally

The migrations reference Supabase's managed `auth` and `storage` schemas, so
they cannot be applied to a bare Postgres as-is. `supabase/tests/00_supabase_stubs.sql`
recreates just enough of both to check schema and policy syntax in a container:

```bash
docker run -d --name bj-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=blujeansz -p 55432:5432 postgres:16-alpine
psql postgresql://postgres:test@localhost:55432/blujeansz -f supabase/tests/00_supabase_stubs.sql
for f in supabase/migrations/*.sql; do psql postgresql://postgres:test@localhost:55432/blujeansz -v ON_ERROR_STOP=1 -f "$f"; done
```

This checks that the SQL is valid and self-consistent. It does not exercise
`auth.uid()`-dependent policy behaviour, which needs a real project.

## Scheduled publishing

Public reads require `published_at <= now()`, so scheduled rows must be promoted
when their time arrives. `publish_due_content()` does that and is scheduled
every 5 minutes via pg_cron where available. If pg_cron is not enabled on the
project, call it from a scheduled edge function instead — the migration prints a
notice rather than failing.
