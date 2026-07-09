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

- `SECRET_KEY`
- `APP_DEBUG` default: `1`
- `DATABASE_URL` default: `sqlite:///./jewellery.db`
- `RUN_MIGRATIONS_ON_STARTUP` default: `1`
- `CORS_ORIGINS` default: `*`
- `CORS_ALLOW_CREDENTIALS` default: `1`
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

## Database migrations

This project uses Alembic for database schema changes. On startup, the app runs `alembic upgrade head` by default when `RUN_MIGRATIONS_ON_STARTUP=1`.

Useful commands:

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe your change"
```

## CI tests

GitHub Actions runs `.github/workflows/tests.yml` on every push and pull request. It tests Python 3.11 and 3.12, installs `requirements.txt`, and runs the pytest suite with coverage.

Run the same check locally:

```bash
TESTING=1 python -m pytest --cov=main --cov=models --cov=schemas --cov=database --cov-report=term-missing -q
```

Run smoke tests against the live Hugging Face API:

```bash
LIVE_API_BASE_URL=https://sureshhari-snchatbot-backend.hf.space python -m pytest tests/test_live_api.py -q
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
