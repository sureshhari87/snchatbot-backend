# Savings reconciliation and audited release

Local revision 0024_savings_hold_review follows 0023_savings_holds. The user
approved release only with fresh provider evidence of no refund or active/lost
dispute, an admin reason and immutable audit history on 2026-10-08.

Release requires savings:manage, a bound posted Razorpay payment, a consistent
stored ledger, strict fresh payment evidence and bounded complete refund/dispute
collections. Matching entities are fetched again; only failed refunds and won
disputes with zero deducted amount qualify. Missing, closed, malformed, excessive
or changing evidence fails closed. No client financial assertion is accepted.

The scheme row lock serializes writes and reviews. Immutable decisions preserve
the original hold. A duplicate key returns its current decision; a reopened case
requires a new review/key. Adverse verified events reopen a released case. Repeated
won events neither automatically release active holds nor reopen released ones.
Releasing one hold leaves other holds active.

Manual holds and unposted/unknown checkouts stay held until their evidence or
adjustment workflow is approved. No reversal, reset or legacy balance import is
provided. Read-only reconciliation does not itself verify a provider or release.

Final local accounting/API/reconciliation/review/migration run: 145 passed.
Focused release run: 20 passed, including one added pagination regression.
Staging configuration/revision regressions: 43 passed and 31 subtests.
Real disposable loopback PostgreSQL accounting/lifecycle/release checks: 14 passed.
An initial release migration test correctly refused downgrade after prior tests
created history; empty-schema checks were reordered, without weakening that guard.
Eight admin savings/reconciliation/release widgets, six direct entry/auth checks
and targeted analysis passed. The customer staging APK build passed.

No remote database migration, deploy or feature enablement is recorded here.
Staging-only verification is authorized. The fresh revision-0019 staging backup
and isolated restore/migration to 0024 passed: all 38 legacy table digests and
5 users/24 products/17 notifications were preserved; nine savings tables are
empty. Source hashes matched and the local server stopped. Render settings,
source pinning and actual test provider/device evidence remain required.
Production and push sending remain unchanged.

Provider reference: https://d6xcmfyh68wv8.cloudfront.net/docs/api/disputes/
The collection adapter requests count/skip pagination and must be exercised
against TEST provider responses before cutover. Unknown pagination is a release
gate, never permission to omit evidence.
