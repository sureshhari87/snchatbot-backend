# Default-off FastAPI referrals

Approved for local implementation on 2026-10-17: 100 points to the referrer and
50 to the new customer on their first verified merchandise order of at least
INR 1,000 after coupon. Tax and delivery are excluded. Voucher/reward redemption
does not reduce this after-coupon eligibility threshold. Other purchase rewards
continue using their existing eligible-paid-merchandise calculation.

REFERRALS_ENABLED defaults to 0 and the API also requires REWARDS_VOUCHERS_ENABLED.
Revision 0034_referrals is local and unapplied. This document authorizes no live
migration, configuration, deployment, provider request, charge or balance import.

## Ownership and immutable terms

The authenticated backend customer owns one random referral code. A customer can
apply one other customer's code before their first verified purchase. Reapplying
the same binding returns its saved state; changing referrer or self-referring is
rejected. Administrators cannot participate. Codes, applications, approved terms,
first-purchase outcomes and credits have database immutability guards. Code/owner
bindings and unique application/outcome/credit constraints are enforced in SQL.

Eligibility uses preserved SQL paid/verified/captured history and posted financial
reservations. An existing payment reference also blocks fresh enrolment pending
history reconciliation. A below-threshold first verified order consumes the
application without awarding on a later order. Frozen terms cannot be supplied
by the client. No existing Firebase reward balances or codes are copied.

## Verified posting and locking

Only the trusted verified financial checkout settlement calls referral posting.
This runs in the same transaction as its purchase credit, redemption and payment
audit. The provider's fresh capture/order/refund/dispute checks remain in force.
Two immutable referral credits use the same payment evidence digest. Credit
balances use integer points and fail closed on overflow. Duplicate capture and
webhook/status recovery cannot create a second outcome or credit pair.

Application takes both customer locks in ascending ID order and creates their
reward accounts before saving the binding. Settlement takes the referred-customer
and reservation locks, then both existing reward accounts in ascending ID order,
then the existing voucher lock. It never takes the referrer's customer lock while
holding account locks. This avoids reciprocal referral account-lock cycles.

Refund/dispute evidence on a rewarded order holds both accounts using the same
account lock order. This remains active even when the programme is later disabled.
There is no automatic release, cash refund, reservation unwind or balance reversal.
If an application exists when the programme is disabled, the first verified
order records a no-award outcome; re-enabling never retroactively credits it.
A pre-existing affected-account hold defers the referral award for audited review;
the held outcome is not automatically retried on a later purchase.

## API and reconciliation

GET /referrals is read-only, authenticated and no-store. The bounded
GET /admin/referrals saved-evidence audit requires rewards:manage, remains
readable with customer financial/programme features disabled and never releases
or adjusts a balance. It exposes no codes or provider references. POST /referrals/code
idempotently creates/returns the caller's own code. PUT /referrals/application
accepts only a code. Responses contain own programme terms, code and aggregate
counts, with no other customer's identity or provider data. Reward history merges
purchase/redemption and referral ledgers with bounded SQL pagination; referral
history IDs use a negative namespace. Referrer history conceals the other
customer's order ID. These history IDs are not mutation addresses.

Read-only reconciliation validates both referral credits, frozen terms, the
qualifying order, its immutable posted audit and the shared provider evidence.
Balances include the separate referral ledger. It never repairs or posts entries.
The payment response field referral_rewards_awarded retains its legacy referrer
meaning (100 points); the new customer receives a separate 50-point credit.
The client loads through an authenticated read first, permitting safe session
renewal; account epochs guard subsequent code writes. Writes are not automatically
replayed after a 401. Session changes clear cached codes and statistics.

## Release gates

Reconcile preserved customer identities, prior verified orders, opening reward
balances and any legacy referral codes/bindings before enabling in production.
No assumption that legacy referrals are test-only is made. Run local transaction,
API, report, migration and disposable PostgreSQL concurrency checks; run matched
Flutter tests/analysis. Take a fresh staging backup and verify an isolated restore
before any separately approved 0034 staging rollout. Existing production, push
sending, online custom-design balance creation and voucher 2 remain unchanged.
The downgrade refuses to discard any referral record.

Before production enablement, review and retire overlapping legacy referral
application/award writes as part of the approved cutover. Preserve or explicitly
resolve legacy code activation, historical terms and existing bindings; do not
let both legacy and SQL paths award the same purchase. The saved catalogue export
did not include referral records and cannot establish that this data is disposable.

## Local verification checkpoint - 2026-10-18

The full backend suite passed 1,296 tests with 81 skipped. The separate final
loopback PostgreSQL run passed 13 referral concurrency/immutability cases; all
disposable servers were stopped. This is local verification, not deployment.
SQL coupon configuration is still unavailable in financial checkout; the approved
after-coupon eligibility formula does not authorize enabling coupon inputs.
