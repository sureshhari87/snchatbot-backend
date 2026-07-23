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
- `GET /ready`
- `GET /mobile/config`
- `GET /products`
- `GET /products/{product_id}`
- `GET /products/{product_id}/similar`
- `GET /featured-products`
- `GET /seasonal-collections`
- `GET /categories`
- `GET /users/me/addresses`
- `POST /users/me/addresses`
- `PATCH /users/me/addresses/{address_id}`
- `DELETE /users/me/addresses/{address_id}`
- `GET /users/me/notification-settings`
- `PATCH /users/me/notification-settings`
- `GET /chat/sessions`
- `GET /chat/sessions/{session_id}`
- `GET /faqs`
- `GET /policies`
- `GET /orders/{order_reference}`
- `POST /orders/{order_reference}/cancel`
- `POST /orders/{order_reference}/return`
- `POST /orders/{order_reference}/refund`
- `POST /chat`
- `POST /register`
- `POST /login`
- `POST /refresh`
- `POST /logout`
- `POST /logout-all-devices`
- `POST /forgot-password`
- `POST /verify-email`
- `GET /verify-email?token=...`
- `POST /resend-verification`
- `POST /reset-password`
- `GET /reset-password?token=...`
- `POST /wishlist`
- `POST /save-for-later`
- `POST /request-callback`
- `POST /appointments`
- `POST /custom-orders`
- `POST /complaints`
- `POST /orders/support`
- `POST /feedback`

Admin endpoints are protected with admin RBAC and cover product CRUD, inventory, categories, featured items, seasonal collections, FAQ/policy content, public app config, leads, support queues, chat analytics, and transcript review.

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

- `EMAIL_PROVIDER` one of `smtp` or `resend`; use `resend` on Hugging Face
- `EMAIL_HOST`
- `EMAIL_PORT` default: `587`
- `EMAIL_USERNAME`
- `EMAIL_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_FROM_NAME` default: `Jewellery Chat`
- `EMAIL_USE_TLS` default: `1`
- `EMAIL_USE_SSL` default: `0`
- `EMAIL_TIMEOUT_SECONDS` default: `10`
- `RESEND_API_KEY` required when `EMAIL_PROVIDER=resend`
- `RESEND_API_URL` default: `https://api.resend.com/emails`

Production integration secrets:

- `OMS_ENABLED` enables real OMS calls when `1`
- `OMS_BASE_URL` base URL for your order-management API
- `OMS_API_KEY` bearer token for the OMS API
- `OMS_TIMEOUT_SECONDS` default: `10`
- `LLM_ENABLED` enables grounded LLM replies when `1`
- `LLM_BASE_URL` OpenAI-compatible base URL, for example `https://api.openai.com/v1`
- `LLM_API_KEY`
- `LLM_MODEL` default: `gpt-4o-mini`
- `LLM_TIMEOUT_SECONDS` default: `20`
- `LLM_MAX_TOKENS` default: `350`
- `MONITORING_WEBHOOK_URL` alert webhook for external monitoring
- `MONITORING_WEBHOOK_TIMEOUT_SECONDS` default: `5`
- `SENTRY_DSN` optional Sentry error monitoring DSN
- `SENTRY_RELEASE` optional release label for Sentry events
- `SENTRY_TRACES_SAMPLE_RATE` default: `0.05`
- `SENTRY_PROFILES_SAMPLE_RATE` default: `0`
- `SENTRY_SEND_DEFAULT_PII` default: `0`
- `ALERT_ERROR_THRESHOLD` default: `5`
- `ADMIN_BOOTSTRAP_ENABLED` set to `1` only for a one-time admin account bootstrap
- `ADMIN_BOOTSTRAP_EMAIL` real admin email address
- `ADMIN_BOOTSTRAP_USERNAME` real admin username
- `ADMIN_BOOTSTRAP_PASSWORD` temporary bootstrap password, remove after first successful startup

For local Gmail SMTP testing, use an app password, not your normal account password. If email is not configured, the app still creates the token and prints the email body in logs for local development.

For Hugging Face Spaces, do not use Gmail SMTP for production email. Spaces network egress is limited to HTTP/HTTPS-style ports, so SMTP ports such as `587` and `465` can fail even when your Gmail credentials are correct. Use an HTTPS email API provider such as Brevo, SendGrid, Mailgun, or Postmark. If Resend returns Cloudflare `1010 browser_signature_banned`, switch to `EMAIL_PROVIDER=brevo`.

For Hugging Face MVP deployment before your Android deep links are ready, use the backend fallback pages:

```env
FRONTEND_VERIFY_URL=https://sureshhari-snchatbot-backend.hf.space/verify-email
FRONTEND_RESET_URL=https://sureshhari-snchatbot-backend.hf.space/reset-password
```

Then add HTTPS email provider secrets one by one. Example with Brevo:

```env
EMAIL_PROVIDER=brevo
BREVO_API_KEY=xkeysib-your-brevo-api-key
BREVO_API_URL=https://api.brevo.com/v3/smtp/email
EMAIL_FROM=your-verified-brevo-sender@example.com
EMAIL_FROM_NAME=Sona Jewellery
EMAIL_TIMEOUT_SECONDS=10
```

After restart, sign in as admin, authorize `/docs`, and execute `POST /admin/email/test`.
If the response is `Email test sent`, register a new customer email and click the
verification link from the inbox.

## Admin Account Bootstrap

Admin routes are protected by RBAC and require a user with `is_admin=True`. Do not use a customer account for admin work.

For Hugging Face, add these as temporary secrets/variables, restart the Space once, confirm admin login works, then remove `ADMIN_BOOTSTRAP_PASSWORD` and set `ADMIN_BOOTSTRAP_ENABLED=0`.

```env
ADMIN_BOOTSTRAP_ENABLED=1
ADMIN_BOOTSTRAP_EMAIL=admin@example.com
ADMIN_BOOTSTRAP_USERNAME=admin
ADMIN_BOOTSTRAP_PASSWORD=replace-with-strong-temporary-password
```

For local or Postgres-backed production creation from Windows CMD:

```cmd
set DATABASE_URL=postgresql+psycopg://db_user:db_password@db-host.example.com:5432/snchatbot?sslmode=require
python scripts\create_admin_user.py --email admin@example.com --username admin
```

The script prompts for the password without printing it. To promote an existing verified customer account only when necessary:

```cmd
python scripts\create_admin_user.py --email admin@example.com --username admin --promote-existing
```

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

## Production Postgres

Use a managed Postgres database for production. Add the provider connection string as the Hugging Face secret `DATABASE_URL`.

Recommended URL format:

```env
DATABASE_URL=postgresql+psycopg://db_user:db_password@db-host.example.com:5432/snchatbot?sslmode=require
```

The app also accepts provider URLs beginning with `postgres://` or `postgresql://` and normalizes them to the `psycopg` SQLAlchemy driver at startup.

Run migrations locally against the production database only when you intentionally want to update that database:

```cmd
set APP_ENV=production
set DATABASE_URL=postgresql+psycopg://db_user:db_password@db-host.example.com:5432/snchatbot?sslmode=require
alembic upgrade head
```

On Hugging Face, keep `RUN_MIGRATIONS_ON_STARTUP=1` so each deployment runs `alembic upgrade head` before serving traffic.

For live releases, review the full backup, migration, staging smoke-test, and rollback checklist in [docs/release-runbook.md](docs/release-runbook.md).

Before real customers use the app, follow [docs/database-backup-plan.md](docs/database-backup-plan.md): create a baseline backup, restore it into a separate test database, and confirm smoke tests pass.

## Optional LLM Layer

The chatbot works with catalogue/rule-based answers by default. A guarded OpenAI-compatible LLM
layer can be enabled later with `LLM_ENABLED=1`, `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`.
It sends only filtered catalogue and FAQ/policy context to the provider, validates the reply, and
falls back to rules if the provider fails or returns unsafe text.

See [docs/llm-layer.md](docs/llm-layer.md) before enabling it in Hugging Face.

For Android app wiring, use [docs/android-screen-api-map.md](docs/android-screen-api-map.md) and [docs/android-retrofit-integration.md](docs/android-retrofit-integration.md).

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

## Android integration contract

Use `GET /mobile/config` first when the Android app starts. It exposes backend capabilities, the chat response fields the app should expect, and public non-secret config values.

Important mobile flows:

- Browse catalogue: `GET /products`, `GET /products/{product_id}`, `GET /products/{product_id}/similar`, `GET /featured-products`, `GET /seasonal-collections`, `GET /categories`
- Auth session: `POST /login`, `POST /refresh`, `POST /logout`, `POST /logout-all-devices`, `GET /me`
- User account: address book, notification settings, wishlist, save-for-later, and saved chat sessions
- Chat: `POST /chat` returns `intent`, `confidence`, `answer_source`, `tool_calls`, `guardrails`, `applied_filters`, `result_count`, `suggested_next_questions`, `lead_captured`, and optional `handoff`
- Customer actions: wishlist, save-for-later, callback requests, appointments, custom-order requests, complaints, and order-support capture
- Orders: `GET /orders/{order_reference}` and cancel/return/refund endpoints call the configured OMS when enabled
- Feedback: `POST /feedback` stores thumbs-up, thumbs-down, not-helpful, rating, and comments against a `response_id`

Order support is capture-only until `OMS_ENABLED=1` and `OMS_BASE_URL` are configured. After that, lookup, cancel, return, refund, and `/orders/support` requests are sent to your OMS and audited in `external_integration_events`. See [docs/oms-integration.md](docs/oms-integration.md) for the required OMS API contract and Android handling notes.

The LLM layer is optional and disabled by default. When `LLM_ENABLED=1`, `LLM_BASE_URL`, and `LLM_API_KEY` are configured, `/chat` sends a grounded catalog prompt to an OpenAI-compatible chat-completions endpoint. If the LLM fails, the backend falls back to the existing deterministic catalog reply.

Admin production operations:

- `GET /admin/integrations/status`
- `GET /admin/integrations/events`
- `POST /admin/alerts/test`

For production monitoring setup, see [docs/monitoring-sentry.md](docs/monitoring-sentry.md).

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
