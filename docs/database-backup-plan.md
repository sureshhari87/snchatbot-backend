# Database Backup And Export Plan

Use this plan before real customers use the chatbot, before every production migration, and before every release that changes models or Alembic migrations.

## Goals

- Prevent customer data loss during deploys, migrations, or provider incidents.
- Keep a restorable copy before every production schema change.
- Make restore testing part of launch readiness, not an emergency-only task.
- Keep database dumps out of Git and local chat history.

## What Must Be Backed Up

Production database:

- users
- auth and refresh token audit rows
- email verification and password reset token rows
- products, categories, featured items, collections
- chat sessions, messages, analytics, feedback
- wishlist and save-for-later items
- callback, appointment, custom order, complaint, and order support rows
- addresses and notification settings
- external integration audit events

Also keep a copy of:

- the Git commit SHA deployed at the time of backup
- the Alembic revision at the time of backup
- Hugging Face variable names used for the deployment, without secret values

## Backup Schedule

Before first real customer:

- Create one manual baseline backup after running `alembic upgrade head`.
- Restore that backup into a separate test database and confirm `/ready` plus smoke tests.

Before every production release:

- Create a pre-release backup.
- Run `alembic current`.
- Run smoke tests after deploy.
- Keep the backup until the next release has been stable for at least 7 days.

Ongoing production:

- Daily automated backup through the database provider.
- Weekly manual export using this repo's backup script.
- Monthly restore drill into a non-production database.

Suggested retention:

- Daily backups: 14 days.
- Weekly backups: 8 weeks.
- Monthly backups: 6 months.
- Pre-migration backups: keep at least 30 days, longer for major schema changes.

## Local SQLite Backup

Use only for development or temporary MVP data.

Windows CMD:

```cmd
set DATABASE_URL=sqlite:///./jewellery.db
python scripts\backup_database.py --label pre-customer-launch
```

Output goes to:

```text
backups\
```

The backup script also creates a `.manifest.json` file with timestamp, source, type, and backup size.

## Production Postgres Backup

Install PostgreSQL client tools locally so `pg_dump` is available.

Recommended Hugging Face secret:

```env
DATABASE_URL=postgresql+psycopg://db_user:db_password@db-host.example.com:5432/snchatbot?sslmode=require
```

Windows CMD:

```cmd
set DATABASE_URL=postgresql+psycopg://db_user:db_password@db-host.example.com:5432/snchatbot?sslmode=require
python scripts\backup_database.py --label pre-release
```

If `pg_dump` is not on PATH:

```cmd
set PG_DUMP_PATH=C:\Program Files\PostgreSQL\16\bin\pg_dump.exe
python scripts\backup_database.py --label pre-release
```

The script creates a custom-format `.dump` file. Custom format is preferred because it supports safer `pg_restore` workflows.

## Restore Drill

Never test restore against production.

Create a new empty test database from your provider dashboard, then restore into that test database.

Example:

```cmd
pg_restore --clean --if-exists --dbname postgresql://test_user:test_password@test-host:5432/snchatbot_restore backups\snchatbot-pre-release-YYYYMMDD-HHMMSS.dump
```

Then point the backend locally or in staging to the restored DB:

```cmd
set APP_ENV=staging
set DATABASE_URL=postgresql+psycopg://test_user:test_password@test-host:5432/snchatbot_restore?sslmode=require
alembic current
python scripts\smoke_test.py
```

Minimum restore checks:

- `alembic current` shows the expected latest revision.
- `/ready` returns `status: ok`.
- `GET /products` returns catalog rows.
- Login works for a test user.
- `POST /chat` returns a valid response.
- Admin analytics endpoint works for an admin token.

## Provider Snapshot

If using Neon, Supabase, Render, Railway, AWS RDS, or another managed Postgres provider, enable provider-level backups/snapshots too.

Minimum provider settings:

- Daily automated snapshots.
- Point-in-time recovery if your plan supports it.
- Alerts for failed backups.
- Separate staging database so restore drills do not touch production.

Manual `pg_dump` exports are still useful before migrations because they are portable across providers.

## Before Running Alembic In Production

Checklist:

- `git status` is clean.
- Latest tests pass.
- `alembic history` reviewed.
- `alembic upgrade head --sql` reviewed for destructive changes.
- Fresh backup exists and is non-empty.
- Backup manifest exists.
- Restore drill has passed at least once before first customer launch.

Commands:

```cmd
python scripts\backup_database.py --label pre-migration
alembic upgrade head
python scripts\smoke_test.py
```

## Security Rules

- Never commit files from `backups\` or `exports\`.
- Never paste `DATABASE_URL` with password into chat, screenshots, README, or GitHub issues.
- Store production DB credentials only in Hugging Face Secrets or the database provider dashboard.
- Rotate the DB password if it is exposed.
- Limit database access by provider firewall/IP allow list where supported.
- Use a separate read-only database user later for analytics exports.

## Launch Gate

Do not open the backend to real customers until all are true:

- Production Postgres `DATABASE_URL` is configured.
- `alembic upgrade head` has run successfully.
- A baseline backup has been created.
- A restore drill has passed on a separate test database.
- Live smoke tests pass against the production API.
- Someone is assigned as backup owner for weekly checks.
