# Database concurrency and payment finalization verification

This change does not require an Alembic migration or larger pool limits.

## Changes

- Main application routes that perform blocking work without `await` use worker
  threads. Health/root remain async. The webhook reads its body asynchronously,
  then offloads database/provider processing as one sequential worker operation.
- In-memory login-failure bookkeeping is protected by a lock for worker access.
- Payment finalization locks and refreshes the order before its idempotency check.
  Verify, webhook and reconciliation share that boundary.
- Inventory rows are locked in product-ID order; the whole basket is validated
  before mutation. A savepoint protects inventory/cart changes if cleanup fails.
- Replayed callbacks cannot finalize an already-finalized order with another
  payment. Notification deduplication remains recipient/order scoped.

## Local verification

Run the ordinary tests with the project's test environment. The opt-in
`tests/test_payment_concurrency_postgres.py` starts and stops a fresh loopback-only
PostgreSQL cluster using `.codex/postgres-tools/pgsql/bin`. It never consumes a
Neon URL and uses synthetic records and mocked provider responses. Enable with
`RUN_LOCAL_POSTGRES_TESTS=1`; no real payment or SMS is sent.

Tests cover mixed concurrent requests using a two-connection pool, verification
before/after webhook delivery, new webhook event IDs for an existing payment,
inventory rollback, concurrent same-order/different-order finalization, and
recipient-scoped notifications for repeated admin status changes.

## Staging release gate

Local verification on 2026-09-28: full suite 364 passed, 7 optional tests skipped,
59 subtests passed. Both opt-in PostgreSQL concurrency cases then passed; the
temporary server stopped cleanly. After the final login bookkeeping lock, the
focused authentication/security/concurrency rerun passed 61 tests. Ruff,
whitespace checks and Bandit on main.py passed. Dependency deprecation warnings
remain. These results are local, not evidence of a staging or production rollout.

Local tests do not establish that the live timeout has disappeared. Deploy only
the reviewed commit to Render staging, retaining Auto-Deploy off. No HF changes.

1. Verify readiness and schema stays at 0017_system_notifications.
2. Fresh OTP login; browse products and rates repeatedly at normal device usage.
   Check logs for SQLAlchemy pool timeouts. Do not flood the service.
3. Make one simulated Razorpay test payment. Compare stock before/after against
   purchased quantities; verify one payment confirmation for the order.
4. Redeliver the existing test webhook using provider-supported controls, without
   creating another payment. Confirm unchanged stock and notification count.
5. For a designated synthetic test order, use the authorized backend admin flow
   to change status to processing/shipped, then repeat the same status. Expect
   one notification per order/status, visible only to the order's customer.

Do not change a real customer's fulfilment status for testing. Production
deployment remains blocked until staging regression and backup gates pass.
