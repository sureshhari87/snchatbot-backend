---
title: Jewellery Chat API
emoji:  💎
colorFrom: pink
colorTo: purple
sdk: docker
app_port: 7860
base_path: /docs
pinned: false
---
# Jewellery Chat API

Backend API for a Flutter jewellery ecommerce assistant.

## Endpoints

- `GET /`
- `GET /health`
- `POST /chat`
- `POST /register`
- `POST /login`
- `POST /forgot-password`
- `POST /verify-email`
- `POST /resend-verification`

## Environment variables

Set these in Hugging Face secrets, or copy `.env.example` to `.env` for local development.

- `APP_ENV` one of `local`, `test`, `staging`, `production`
- `SECRET_KEY`
- `APP_DEBUG` default: `1`
- `DATABASE_URL` default: `sqlite:///./jewellery.db`
- `RUN_MIGRATIONS_ON_STARTUP` default: `1`
- `CORS_ORIGINS` default: `*` for local/test, empty for staging/production unless set explicitly
- `CORS_ALLOW_CREDENTIALS` default: `1`
- `MAX_REQUEST_BODY_BYTES` default: `1048576`
- `HTTPS_REDIRECT` default: `1` in staging/production, `0` in local/test
- `TRUSTED_HOSTS` comma-separated public API hostnames for staging/production
- `PROXY_HEADERS` default: `1`
- `FORWARDED_ALLOW_IPS` default: `*`
- `HOST` default: `0.0.0.0`
- `PORT` default: `7860`
- `WEB_CONCURRENCY` default: `1`
- `UVICORN_LOG_LEVEL` default: `info`
- `RUN_MIGRATIONS_BEFORE_START` default: `0`
- `ACCESS_TOKEN_EXPIRE_MINUTES` default: `30`
- `REFRESH_TOKEN_EXPIRE_DAYS` default: `7`
- `PASSWORD_RESET_EXPIRE_MINUTES` default: `15`
- `EMAIL_VERIFICATION_EXPIRE_MINUTES` default: `30`
- `FRONTEND_VERIFY_URL`
- `FRONTEND_RESET_URL`

Email secrets for registration verification and password reset:

- `EMAIL_HOST`
- `EMAIL_PORT` default: `587`
- `EMAIL_USERNAME`
- `EMAIL_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_FROM_NAME` default: `Jewellery Chat`
- `EMAIL_USE_TLS` default: `1`
- `EMAIL_USE_SSL` default: `0`
- `EMAIL_TIMEOUT_SECONDS` default: `10`

For Gmail, use an app password, not your normal account password. If SMTP is not configured, the app still creates the token and prints the email body in logs for local development.

## Settings profiles

`APP_ENV` selects environment-specific defaults:

- `local`: debug on, local SQLite database, migrations enabled
- `test`: debug off, in-memory SQLite defaults, migrations disabled
- `staging`: debug off, staging database defaults, migrations enabled, explicit CORS origins expected
- `production`: debug off, production database expected from `DATABASE_URL`, migrations enabled, explicit CORS origins expected

Example files are included for each profile: `.env.local.example`, `.env.test.example`, `.env.staging.example`, and `.env.production.example`.

## Database migrations

This project uses Alembic for database schema changes. On startup, the app runs `alembic upgrade head` by default when `RUN_MIGRATIONS_ON_STARTUP=1`.

Useful commands:

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe your change"
```

For live releases, review the full backup, migration, staging smoke-test, and rollback checklist in [docs/release-runbook.md](docs/release-runbook.md).

## Production container

The Docker image runs `scripts/start.sh`, which starts Uvicorn with production ASGI settings from environment variables. In staging and production, run behind a reverse proxy or platform load balancer that terminates HTTPS and forwards `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto`.

Build locally:

```cmd
docker build --pull=false -t snchatbot-backend:ci .
```

Run locally with an env file:

```cmd
docker run --env-file .env.production -p 7860:7860 snchatbot-backend:ci
```

## CI tests

GitHub Actions runs `.github/workflows/tests.yml` on every push and pull request. It checks formatting and linting with Ruff, tests Python 3.11 and 3.12, fails below 80% coverage, runs Bandit and pip-audit security scans, checks for committed secrets, validates the Docker image build, runs staging smoke tests before production, and only reaches production deploy after staging passes.

Install local development tools:

```cmd
python -m pip install -r requirements-dev.txt
```

Run formatting and lint checks:

```cmd
ruff format --check .
ruff check .
```

Run the full test suite with the same coverage threshold:

```cmd
set APP_ENV=test
set TESTING=1
set APP_DEBUG=0
set SECRET_KEY=ci-test-secret-key
set DATABASE_URL=sqlite://
set RUN_MIGRATIONS_ON_STARTUP=0
set FRONTEND_VERIFY_URL=http://localhost:3000/verify-email
set FRONTEND_RESET_URL=http://localhost:3000/reset-password
set MAX_REQUEST_BODY_BYTES=1048576
python -m pytest --cov=main --cov=models --cov=schemas --cov=database --cov=config --cov-fail-under=80 --cov-report=term-missing -q
```

Run security and secret checks:

```cmd
bandit -c pyproject.toml -r .
pip-audit --progress-spinner off -r requirements.txt
python scripts\secret_hygiene_check.py
```

Validate the Docker build when Docker Desktop is installed:

```cmd
docker build --pull=false -t snchatbot-backend:ci .
```

The `deploy-staging` CI job is ready but only triggers on pushes to `main` after the `STAGING_DEPLOY_WEBHOOK` GitHub secret is configured for the `staging` environment.
The `staging-smoke-tests` job requires `STAGING_BASE_URL`; production deployment is blocked until that URL is configured and smoke tests pass.

Set these GitHub environment secrets when the deploy targets are ready:

- `STAGING_DEPLOY_WEBHOOK`
- `STAGING_BASE_URL`
- `PRODUCTION_DEPLOY_WEBHOOK`

Run smoke tests against the live Hugging Face API:

```cmd
set LIVE_API_BASE_URL=https://sureshhari-snchatbot-backend.hf.space
python -m pytest tests\test_live_api.py -q
```

After deploying the latest chat changes, also verify the newest chat response contract:

```cmd
set LIVE_API_BASE_URL=https://sureshhari-snchatbot-backend.hf.space
set LIVE_API_EXPECT_LATEST_CHAT_CONTRACT=1
python -m pytest tests\test_live_api.py -q
```

## Chat buying suggestions

The chat response includes a `suggestions` list with common buying questions:

- Show me gold rings under 20000
- Suggest a ring for engagement
- Which jewellery is best for daily wear?
- Gift ideas for mom under 15000
- Gift ideas for wife on anniversary
- Show earrings for office wear
- Suggest a necklace for a wedding saree
- What can I buy for a birthday gift?
- Show lightweight gold jewellery
- Show jewellery under 10000
- Suggest rose gold rings
- Show pearl earrings
- Which necklace suits a round face?
- Suggest jewellery for a bride
- Show simple studs for daily use
- Compare gold and silver jewellery
- What size ring should I buy?
- Show jewellery for a girlfriend
- Suggest a pendant for everyday wear
- Show premium jewellery above 20000
- What jewellery is good for sensitive skin?
- Show traditional earrings
- Suggest minimalist jewellery
- Show jewellery for party wear
- Which jewellery has good resale value?
- Do you have certified gold jewellery?
- Show matching necklace and earrings
- What is best for a first jewellery purchase?
- Suggest jewellery for sister under 12000
- Show in-stock jewellery only

## Example POST /chat
```json
{
  "message": "show me gold rings under 20000",
  "user_id": "user_1",
  "session_id": "session_1"
}
```

The chat response includes a `response_id`. Send that ID with feedback so thumbs-up, thumbs-down, and not-helpful signals are tied to the exact bot answer:

```json
{
  "response_id": "response-id-from-chat",
  "feedback_type": "not_helpful",
  "comment": "I wanted lighter daily-wear designs"
}
```

Admin analytics endpoints:

- `GET /admin/analytics/chat`
- `GET /admin/chat-transcripts/review`
- `PATCH /admin/chat-transcripts/{response_id}/review`

Use these to review unmatched queries, low-conversion searches, top intents, top filters, repeat users, requested products, and transcripts that need prompt or recommendation improvements.
