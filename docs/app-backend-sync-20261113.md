# App/backend source alignment - 13 November 2026

This branch prepares a combined backend for the November Flutter changes.
It does not migrate or deploy a hosted database, activate features, send
notifications, or make payment or AI-generation requests.

## Sources reviewed

| Repository | Branch | Source |
| --- | --- | --- |
| Backend | `codex/fastapi-commerce-migration` | `f79075a` |
| Backend | `codex/billing-gst-staging-20261108` | `6ae8744` |
| Flutter | `codex/production-preflight-20261112` | `ab2f143` |
| Flutter recovery record | `codex/production-recovery-review-20261112` | `bcdd835` |

The Flutter recovery branch changes documentation and the configuration
proposal; its runtime API implementation matches the preflight source.
GitHub source was inspected directly. The older, locally modified Flutter
checkout was not merged or replaced.

Both backend histories are combined on `codex/backend-app-sync-20261113`.
The resolved runtime matches `6ae8744`; contact-email quotas, purchase identity,
cancellation race coverage, and the combined catalogue/AI fixes are preserved.

## Compatibility checks

The customer app and admin console require authenticated routes for:

- Backend admin capabilities and session recovery.
- Customer contact profiles and email verification, separate from OTP identity.
- Unread notification counts and restock navigation.
- Payment receipts, savings receipts, issued sales documents and invoice PDF data.
- Buyer verification and customer/admin cancellation review.

`tests/test_flutter_release_contract.py` protects the HTTP methods and
authentication declarations for these release routes. Existing domain tests
cover permissions, ownership, feature gates, amounts and immutable records.

The PostgreSQL billing migration/restore test now honors `LOCAL_POSTGRES_BIN`
and platform executable names, and runs in the billing PostgreSQL CI job.
Pytest discovery is scoped to `tests` so local recovery copies under `backups`
cannot shadow the current test modules.

## Deployment state and next steps

The source migration head is `0042_b2c_gst`. The app's 12 November production
preflight records the existing HF source as `23f1352` and production Neon as
`0019_push_outbox`. These are recorded observations, not a fresh live check.

The app's recovery record confirms a protected backup and an isolated
`0019` to `0042` rehearsal passed using exact backend source `6ae8744`.
This is preparation evidence; refresh the agreed recovery/reconciliation
evidence at the actual write freeze. Never restore an older archive over
later customer activity without preserving that later activity.

Before production rollout, complete the remaining configuration and business
decisions in the app's production handoff: customer/catalogue/stock and financial
reconciliation, voucher-key recovery, applicable written billing approvals,
courier acceptance, public pages/app links and signed distribution, and live AI
acceptance. The last AI evaluation record reported exhausted API credits;
configured model metadata alone does not establish generation quality.

Use a matched app build and backend commit for staging acceptance. An existing
production dependency response or the five legacy smoke tests does not cover
these newly added routes. Do not use a blanket production migration/push as
the next step solely because the local suite passes.

Keep database exports, credentials, local import/cutover scripts and machine
environments out of this review commit. Existing local files remain untouched.

## Local verification

- Full suite: 1,736 passed, 104 skipped, 95 subtests passed. The skipped cases
  include live API checks and opt-in PostgreSQL tests.
- CI-scoped coverage: 83.46%, passing the unchanged 80% threshold. This is
  coverage of main/models/schemas/database/config, not every backend module.
- Separate disposable PostgreSQL run: all five billing B2C, document concurrency,
  migration/restore and preservation cases passed using PostgreSQL 18.
- The 15 Flutter route/authentication checks are included in the full-suite total.
- Ruff lint/format, Bandit, tracked-source secret hygiene, dependency consistency
  and workflow YAML parsing passed. Alembic has one head: `0042_b2c_gst`.

The local full run used four pytest workers and an explicitly supplied synthetic
test key; that short test key caused PyJWT warnings. An existing raw-body
HTTP-client deprecation also remains. No production secret strength or live
provider behavior is established by this run. Hosted CI and matched-device
acceptance remain separate checks.
