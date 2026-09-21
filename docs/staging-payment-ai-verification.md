# Staging payment and AI verification

Configuration loaded is not evidence of provider authentication or event delivery.
Never use live payment keys, production database credentials, or production webhooks here.

## Optional hidden-input setup

Use the existing staging interpreter from the backend checkout:

```powershell
& "C:\Users\sures\snchatbot-staging\.venv\Scripts\python.exe" scripts/local_staging.py --test-payments --onhand-sms --test-webhook --ai
```

Omit `--ai` until the provider, accessible model and cost limits are agreed.
Omit `--test-webhook` until a dedicated test webhook secret is ready.
The webhook flag requires `--test-payments`; no secrets are accepted as command-line arguments.
All provider settings use hidden prompts and are passed only to the child environment.
The launcher does not inherit production provider credentials or save the entered values.
Reuse the existing staging phone pepper to preserve customer identity.

This launcher remains bound to `127.0.0.1:8001`. It does not create a public tunnel or
configure a Razorpay dashboard endpoint. To test actual provider delivery, configure an
approved public HTTPS staging backend with the same dedicated test webhook secret and
Razorpay TEST keys. Do not expose the local backend or point a test event at production
without an explicitly reviewed deployment configuration.

Webhook path: `/payments/razorpay/webhook`.
Confirm valid signatures are accepted, bad signatures rejected, duplicate capture events
do not repeat stock deductions/order finalization, and a late failure does not undo a paid order.
Use provider delivery logs and backend order/payment state as evidence, not just an SDK callback.

## AI verification

Supply the approved HTTPS provider base URL, a model accessible to that account, and a
restricted staging API key. The launcher sends no provider request during startup.
Readiness reporting `llm.status=configured` proves settings are loaded, not that billing,
model access or generation works. Test an authenticated catalogue question and verify
`answer_source=llm_grounded_catalog` plus an `llm_completion` tool call. Also test provider
failure and confirm a safe rules fallback, with no fabricated prices, stock or payment links.

## Remaining release gates

- Verify approved delivery PIN rules in `commerce.delivery_routes`; test supported,
  unsupported, cutoff, holiday and out-of-stock cases. Do not import test fixtures as policy.
- Complete customer/admin feature and historical-data migration, including financial balances.
- Run phone OTP, wishlist/cart, delivery, test payment success/cancel/recovery, order and stock checks.
- Obtain a fresh production backup through the hidden-input backup tool; restore to a separate
  database and verify schema and data. An archive listing alone is not a restore drill.
- Deploy only after all gates pass and a rollback target is recorded.

## Offline checks

```powershell
python -m unittest tests.test_local_staging tests.test_onhand_staging tests.test_staging_integrations -v
```

These tests contact no providers and perform no database migration. They do not replace
on-device acceptance, actual delivery-policy verification, or a live webhook delivery test.

## Checkpoint: 2026-09-21

- Local `/ready` returned `ok`, including database health. SMS and Razorpay checkout
  settings were loaded; webhook was not configured and LLM was disabled.
- The authorized USB phone was connected and `tcp:8001` reverse forwarding was restored.
- The staging products endpoint returned 24 products, including the four known demo rows.
  No inventory was changed and no new catalogue import ran.
- Existing commerce, order, production-integration and release-integration regression
  suites passed: 52 tests. Providers were mocked; the database was isolated in memory.
- Launcher configuration suites passed: 18 tests. Ruff and secret hygiene checks passed.
- New optional prompts have not been used against the running staging process.
- Actual delivery policy, provider webhook delivery, AI provider access, full phone checkout,
  remaining feature/data migrations and a fresh production backup/restore are NOT verified.
- No production migration or deployment was performed.
