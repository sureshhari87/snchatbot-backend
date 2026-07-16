# Release Runbook

Use this checklist before every staging or production release.

## 1. Build The Same Image For Every Environment

Build once, then promote the same image from staging to production.

```bash
docker build --pull=false -t snchatbot-backend:<release-tag> .
```

Runtime config must come from environment variables or platform secrets, not code edits.

Required production-style variables:

```text
APP_ENV=production
APP_DEBUG=0
SECRET_KEY=replace-with-production-secret
DATABASE_URL=<database-url>
CORS_ORIGINS=https://your-frontend.example
TRUSTED_HOSTS=your-api.example
HTTPS_REDIRECT=1
PROXY_HEADERS=1
FORWARDED_ALLOW_IPS=*
HOST=0.0.0.0
PORT=7860
WEB_CONCURRENCY=2
```

## 1a. Admin Account And RBAC Gate

Create a real admin user before public launch. Do not use customer accounts for admin work.

One-time Hugging Face bootstrap:

```text
ADMIN_BOOTSTRAP_ENABLED=1
ADMIN_BOOTSTRAP_EMAIL=<admin-email>
ADMIN_BOOTSTRAP_USERNAME=<admin-username>
ADMIN_BOOTSTRAP_PASSWORD=replace-with-strong-temporary-password
```

After the Space restarts and the admin can log in:

- set `ADMIN_BOOTSTRAP_ENABLED=0`
- remove `ADMIN_BOOTSTRAP_PASSWORD`
- keep the admin credentials in your password manager
- use a separate customer test account for customer journeys

For Postgres-backed production creation from a trusted machine:

```bash
python scripts/create_admin_user.py --email <admin-email> --username <admin-username>
```

Confirm admin protection:

- a normal customer token receives `403` on `/admin/products`
- the admin token can access `/admin/metrics`

## 2. Reverse Proxy And HTTPS

Terminate HTTPS at the platform load balancer or reverse proxy. Forward traffic to the app container over the internal network.

The proxy must send:

- `X-Forwarded-Proto: https`
- `X-Forwarded-For`
- `Host`

The app container should run with:

```text
PROXY_HEADERS=1
FORWARDED_ALLOW_IPS=<proxy-ip-or-*>
HTTPS_REDIRECT=1
TRUSTED_HOSTS=<public-api-host>
```

For Nginx-style reverse proxies, use the same idea:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_pass http://snchatbot-backend:7860;
```

## 3. Migration Review

Before release:

```bash
alembic current
alembic history
alembic upgrade head --sql
```

Review the generated SQL for destructive operations. For risky changes, create a manual rollback plan before continuing.

Apply migrations during a controlled release step:

```bash
alembic upgrade head
```

`RUN_MIGRATIONS_ON_STARTUP=1` remains available as a safety net, but production releases should run migrations deliberately before shifting traffic.

## 4. Backup Before Migration

Create a backup before applying production migrations.

See the full backup, export, retention, and restore-drill plan in [database-backup-plan.md](database-backup-plan.md).

SQLite:

```bash
python scripts/backup_database.py --label pre-migration
```

PostgreSQL:

```bash
python scripts/backup_database.py --label pre-migration
```

Keep at least the latest successful pre-release backup, verify the file is non-empty, and run a restore drill before the first customer launch.

## 5. Staging Smoke Tests

Deploy to staging first, then run:

```bash
SMOKE_BASE_URL=https://your-staging-api.example \
SMOKE_EXPECT_LATEST_CHAT_CONTRACT=1 \
python scripts/smoke_test.py
```

GitHub Actions runs this automatically before production deployment. `STAGING_BASE_URL` is required in the `staging` environment, and production is blocked if smoke tests cannot run.

## 6. Production Deploy

Production deployment should only happen after:

- quality checks pass
- tests pass with coverage above 80%
- Bandit and pip-audit pass
- Docker build succeeds
- staging deploy succeeds
- staging smoke tests pass
- database backup is complete
- migrations have been reviewed and applied

Configure `PRODUCTION_DEPLOY_WEBHOOK` in the GitHub `production` environment when the production deploy target is ready.

## 7. Rollback Procedure

Fast rollback:

1. Repoint production to the previous known-good image tag.
2. Confirm `/health` and `/ready` return `200`.
3. Run `python scripts/smoke_test.py` against production.
4. Review logs for auth, chat, and database errors.

Database rollback:

1. Prefer forward fixes for already-live customer data.
2. If the release has not accepted new writes, restore the pre-release backup.
3. If an Alembic downgrade is safe and tested, run `alembic downgrade <previous_revision>`.
4. Confirm `alembic current`, `/ready`, and smoke tests.

Never downgrade a production database without checking whether customer data created after the release would be lost.
