# Savings accounting foundation (local only)

> Superseded implementation checkpoint: see [Savings API and app preview](../../../docs/savings-api-and-preview.md). The accounting results below are historical; authenticated APIs and disabled app previews now exist locally.

Approved on 2026-10-06: manual and Razorpay payments, confirmation-time DigiGold
rates, monthly maturity after 11 calendar months AND 11 confirmed installments,
DigiGold maturity after 330 elapsed days, and the existing bonus tiers.
Quantity approval: integer micrograms, rounding purchased gold and each bonus
down separately. Display formatting may use four decimal places.

## Implemented in this step

- Savings scheme, payment, rate snapshot, ledger and audit models.
- Local revision 0020_savings_ledger, following 0019_push_outbox.
- Idempotent enrollment and payment requests with changed-input rejection.
- INR integer paise; server-owned monthly amounts and installment identity.
- Manual admin confirmation; captured Razorpay evidence verification against an
  owned, pre-bound provider order. No client payment object or fallback accepted.
- DigiGold rate selected at posting from server rate snapshots with matching
  24K 995 purity and an explicit validity window. Entries retain rate ID and bonus.
- India calendar month arithmetic with month-end clamping; DigiGold uses elapsed
  24-hour days. Bonus tiers use completed elapsed days at posting, preserving the
  approved legacy timing basis.
- Row locks on schemes, customer locks for request-key races, and unique payment
  references/installments/ledger event keys. Balances derive from ledger entries.
- Atomic posting and redemption with actor/reference audit records. Repeated
  confirmation/redemption returns the original entry; changed references fail.
- Redemption checks maturity, confirmed installments, pending requests and balance
  inside its transaction. Monthly redemption records the one-installment store
  bonus; DigiGold redemption records the gold and bonus quantities.
- Migration-installed PostgreSQL/SQLite triggers reject UPDATE/DELETE of ledger,
  audit and gold-rate snapshots. Corrections must use reviewed append-only flows.
- Downgrade refuses to drop any nonempty savings table.

The internal service functions require authenticated callers and trusted server
time. Callers own commit/rollback; an integrity conflict requires rollback.
Full-admin checks in this foundation are conservative. API permission checks
must use the existing backend RBAC before supporting delegated operator roles.

No savings API routes are installed. No checkout creation, app cutover, legacy
import, remote migration, or deployment has occurred. The registered models add
metadata only; they do not open connections or run migrations.

## Remaining before API/app cutover

1. Add authenticated customer and admin contracts with strict forbidden-extra
   input schemas, bounded pagination and explicit ownership/RBAC checks.
2. Add an audited, validated rate publishing API. Rate provenance and validity
   must be supplied by the operator; do not invent a rate or silently import
   client-controlled Firestore pricing.
3. Create/bind Razorpay orders using server amounts, with safe handling for an
   unknown provider outcome, checkout signature verification and verified
   webhook dispatch/reconciliation. The existing commerce endpoints must keep
   their current contract; savings must not masquerade as a merchandise order.
4. Add pending-request cancellation/rejection and audited refund/correction
   reversal contracts. Posted entries must never be edited to undo a payment.
5. Wire customer/admin Flutter savings screens; preserve existing test records
   until a reviewed reconciliation approach is approved.
6. Complete endpoint, webhook and device testing before separately authorized
   staging backup/schema/deployment work. Production remains gated.

## Verification

Synthetic SQLite accounting and migration tests cover amounts, identity,
idempotency, audit, rollback, rate/bonus boundaries, maturity and preservation.
Opt-in tests use the existing disposable loopback PostgreSQL fixture, never
DATABASE_URL or Neon. They cover duplicate posting, receipt reuse, enrollment,
installment requests, duplicate redemption, posting versus redemption and
database immutability. See tests/test_savings*.py.

Existing checkout/migration regressions are also checked. Final test counts are
recorded in the workspace checkpoint: 56 savings tests passed, including seven
real local PostgreSQL tests; six existing checkout/migration regressions passed.
Ruff and backend diff whitespace checks passed.
