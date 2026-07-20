# Monitoring And Sentry Setup

This backend supports three production monitoring layers:

- structured JSON logs in Hugging Face logs
- optional webhook alerts for urgent operational events
- optional Sentry error and performance monitoring

## Hugging Face Variables And Secrets

Add these in Hugging Face Space settings.

Use Secrets for:

```text
SENTRY_DSN=<your-sentry-python-project-dsn>
MONITORING_WEBHOOK_URL=<your-alert-webhook-url>
```

Use Variables for:

```text
SENTRY_RELEASE=snchatbot-backend-production
SENTRY_TRACES_SAMPLE_RATE=0.05
SENTRY_PROFILES_SAMPLE_RATE=0
SENTRY_SEND_DEFAULT_PII=0
MONITORING_WEBHOOK_TIMEOUT_SECONDS=5
ALERT_ERROR_THRESHOLD=5
```

Keep `SENTRY_SEND_DEFAULT_PII=0` unless you have a documented privacy reason and consent flow.

## What Gets Sent To Sentry

The app captures:

- unhandled application exceptions
- HTTP 5xx exceptions
- an admin-triggered test message
- FastAPI and Starlette request/performance data when Sentry is configured

Before sending manual event context, the app filters obvious sensitive keys such as:

- `authorization`
- `cookie`
- `password`
- `access_token`
- `refresh_token`
- `api_key`
- `database_url`
- `dsn`

## Admin Verification

After setting `SENTRY_DSN`, restart the Space and check logs for:

```text
monitoring.sentry_configured
```

Then log in as admin and call:

```text
POST /admin/alerts/test
```

Expected responses:

```text
Monitoring alert sent (webhook=true, sentry=true)
Monitoring alert sent (webhook=false, sentry=true)
Monitoring alert sent (webhook=true, sentry=false)
Monitoring webhook and Sentry are not configured
```

If Sentry is configured correctly, the test message should appear in your Sentry project.

## Health And Readiness

Use these endpoints for uptime checks:

```text
GET /health
GET /ready
GET /dependencies
```

Use `/health` for a simple process check.

Use `/ready` for production readiness because it validates database connectivity and required schema tables.

Use `/dependencies` to inspect optional services:

- email
- OMS
- LLM
- monitoring

## Recommended Sentry Alert Rules

Create alert rules in Sentry for:

- new issue created in production
- more than 5 errors in 10 minutes
- HTTP 500 errors on auth, chat, order, or admin routes
- p95 transaction duration above your latency target
- repeated OMS failure events after OMS is enabled

## Operational Checks

Daily during MVP testing:

- check `/ready`
- check Hugging Face logs for `error.unhandled`
- check Sentry new issues
- check `/admin/metrics`
- check `/admin/integrations/events?service=oms` after order integration work

Before each production release:

- confirm `SENTRY_DSN` is configured
- confirm `SENTRY_SEND_DEFAULT_PII=0`
- run `POST /admin/alerts/test`
- run live API smoke tests
- confirm database backup exists

