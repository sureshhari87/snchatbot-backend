# Rewards and vouchers migration audit

Inspected and business rules approved 2026-10-10. The local ledger/rules foundation
is implemented; API/payment/Flutter integration and financial enablement are pending.
No database migration, balance import, deletion, reset or deployment is authorized
by this audit. Preserve all legacy records even though the user described these
areas as test data.

## Active paths and current behavior

- Home and notifications can open GiftVoucherScreen; its Redeem action opens cart.
- Cart reads Firestore rewardPoints and gift_vouchers. Checkout sends requested
  reward points and voucher codes to the backend.
- The SQL catalogue checkout in commerce.validate_checkout currently returns
  HTTP409 if a coupon, gift voucher or requested reward points is supplied.
  It does not implement SQL reward/voucher balances or redemption.
- GiftVoucherScreen creates inactive pending_payment Firestore records with
  client-supplied opening balance, random codes, purchaser/recipient details and
  expiry 365 days from request creation. No verified funding step occurs there.
- Current denominations in rupees: 500, 1000, 1500, 2000, 2500, 5000, 10000,
  50000, 100000. Quantity is clamped to 1..100 on the client.
- The legacy admin screen can create active vouchers with arbitrary positive
  integer rupee amount and an optional assigned customer. Default admin expiry
  differs from the customer screen (180 days, with an editable date).
  Its activation switch changes active/status without verifying payment.
- Client voucher validation restricts assignedUserId when present. Recipient
  email alone is not evidence that a customer owns the voucher.
- RewardService directly increments/decrements Firestore rewardPoints, but no
  callers were found in the inspected lib and sona_admin/lib paths.
  This unused helper is not, by itself, proof of an active write dependency.
- Legacy Firestore-backed payment finalization awards purchase/referral points
  and consumes points transactionally. It is a separate path from SQL checkout;
  its existence does not establish SQL migration completion.
- Existing reward_points_coupon_e2e_test.md targets Firestore fixtures/orders.
  Preserve it as historical guidance; it is not SQL migration acceptance proof.

## Approved business rules (2026-10-10)

The user approved the proposed rules unchanged, including activation-based expiry,
and subsequently approved the three checkout/adverse-event proposals:

1. Earn one point per full INR100 of verified discounted jewellery merchandise;
   one point redeems INR1, capped at the eligible merchandise balance.
   Exclude savings, voucher funding and custom-design advances. Referral bonuses
   and reward expiry require separate approval and are deferred.
2. Purchased vouchers support verified Razorpay capture and audited manual
   confirmation. Complimentary admin issuance requires a separate audited reason.
   An unrestricted activation switch cannot activate unfunded purchased vouchers.
3. Allow partial redemption and no cash exchange. A code holder may redeem unless
   the voucher is explicitly assigned to a customer. Validity is 365 elapsed days
   from verified activation, replacing the old request-creation expiry rule.
4. Apply coupon, then voucher, then reward points. Cap each at the remaining
   merchandise amount; tax and delivery remain payable and do not earn rewards.
5. Preserve at least INR1 payable via the existing verified payment flow,
   reducing redemption as necessary. True zero-payment checkout is deferred.
6. Refund/dispute evidence holds the affected reward account or voucher for
   audited review. No automatic release, cash refund or balance adjustment;
   adjustment/release rules need separate approval.

Reservation expiry/release and provider-payment races still need technical
integration/verification. Do not release spendable reservations on a client
cancellation or unknown provider outcome. Preserve legacy test records; no
opening-balance import/reset or production enablement was approved.

## Local implementation checkpoint

- rewards_vouchers_rules.py implements strict bounded integer-paise arithmetic,
  stacking, INR1 minimum, tax/delivery exclusions, earning thresholds, excluded
  purchase purposes and activation-based UTC expiry.
- rewards_vouchers_models.py and draft revision 0027_rewards_vouchers_ledger add
  server-owned accounts/vouchers, signed ledger deltas and audited holds.
  Constraints reject negative/over-reserved balances and unfunded pending
  voucher balances, require actors/reasons for manual/complimentary funding,
  permit only one funding entry per voucher and one redemption per voucher/order.
- The migration protects financial entries and holds against UPDATE/DELETE and
  refuses downgrade when any new financial record exists.
- Alembic metadata registers the models. The migration has NOT been applied.
  Live staging remains at revision 0026_custom_design_advances and backend e43ecf7.
- 154 financial tests passed: 138 local arithmetic, real SQLite migration and
  transaction tests, plus 16 opt-in tests on disposable loopback PostgreSQL.
  PostgreSQL tests never consume DATABASE_URL or contact staging/Neon. The
  cluster was stopped successfully during fixture cleanup. Targeted Ruff passed.
- Internal reservation/posting primitives reserve only available balances,
  scope request keys to the authenticated customer, bind a provider receipt and
  local order, and post debit/credit entries once. Posting rechecks provider
  amount/currency/capture binding and recomputes earnings from the frozen quote.
  Callers own the transaction; downstream failure rolls back the entire posting.
- Draft revision 0028_financial_reservations freezes quote amounts/identity,
  provider bindings after checkout, activation expiry and voucher identity.
  Payment states move reserved -> ready -> posted. Audit entries are immutable,
  and downgrade refuses to remove populated reservations.
- Audited holds are idempotent, require an administrator for manual review,
  and block new spending and unposted settlement. Holds never clear reservations
  or adjust balances. A provider adapter must authenticate and bind adverse
  evidence before applying a refund/dispute hold; the financial adapter now verifies evidence before holding.
- Parallel tests prove customer reservations cannot over-reserve reward points,
  two customers cannot double-spend a bearer voucher, duplicate captures/holds
  create one posting/audit, and hold/reservation races preserve value.
- Revisions 0027/0028 remain unapplied to staging/production. These tests use
  synthetic isolated databases; they are not provider/device acceptance.
  Fresh backup/isolated restore and complete migration preservation acceptance
  remain required before an authorized staging rollout.
- Customer/admin APIs and a gated payment adapter are implemented locally below.
  Flutter migration and live rollout remain pending.
  Referral/expiry, release and cash adjustment features are not implemented.

## Implementation constraints and acceptance

Use authenticated SQL customer IDs and server-owned balances. Money uses integer
paise; reward points use integers. Unique event/idempotency keys bind financial
postings to verified order/provider evidence. Reserve redemption transactionally
before checkout and settle it exactly once with verified payment; do not create
unfunded spendable voucher balances or double-spend across concurrent orders.
Reservations cannot be released solely because a client reports cancellation;
account for provider status and late capture before authorizing spending again.

Persist immutable ledger/audit evidence for funding, redemption, reservations,
holds and any separately approved adjustments. Enforce RBAC, recipient assignment,
expiry and ownership on every endpoint. Fail closed on unknown provider outcomes.
Test concurrent redemption, duplicate/out-of-order callbacks, wrong owner/order/
amount/currency/signature, insufficient balance, expiry and adverse events.

Keep new flows behind default-off flags until policy and staging acceptance pass.
A rewards/voucher staging rollout requires its own reviewed source, fresh backup,
isolated restore, migration/deployment evidence and explicit rollout scope.
Do not deploy production or alter existing staging push flags.


## Local voucher funding and payment APIs (unapplied 0029)

Authenticated customer APIs now support one voucher per manual or Razorpay funding
request, listing/history, explicit code recovery, validation, checkout, verification
and refresh. Admin APIs require vouchers:manage or rewards:manage and provide audited
manual confirmation, complimentary issuance, holds and reconciliation. Funding
never earns rewards. Activation starts the approved 365-day expiry, with one ledger
credit and posting audit. Bearer codes use 192 bits of randomness: only their hash
and encrypted recovery copy persist. Codes are returned only by the explicit
owner/assignee endpoint with no-store caching; they never enter provider notes.

REWARDS_VOUCHERS_ENABLED defaults to 0. VOUCHER_CODE_ENCRYPTION_KEY must be supplied
privately before issuing codes; retain that key for recovery. The example contains
only a blank placeholder. Revisions 0027, 0028 and 0029 remain unapplied to live
staging and production. No Flutter screens or live financial settings changed.

The SQL /payments/razorpay/orders and verification flow now dispatch to a durable
financial checkout when enabled. It requires a stable request_key, freezes server
catalogue items and totals, reserves value before provider creation, and posts the
ledger, stock, cart cleanup, order state and notification in one transaction.
Coupon rules are still unconfigured and rejected; current SQL tax/delivery remain
zero. The approved rules support separate charges but this phase invents none.
Use /financial/checkout/quote for authoritative preview and the saved reservation
refresh/admin reconciliation APIs for uncertain or callback-loss outcomes.

A saved provider attempt is never replaced after a timeout. Only a freshly verified
unattempted order can reopen checkout; a paid order recovers its original posting.
Attempted/failed orders require review; no new-attempt or reservation-release policy
is implemented. A voucher expiring after reservation blocks settlement and keeps
its reservation for review. No automatic release, refund or balance adjustment
exists. Holds remain held even after later clean provider reads.

/financial/payments/razorpay/webhook validates the raw-body signature, then obtains
fresh authenticated order, payment and refund/dispute evidence. The existing generic
payment webhook also dispatches bound financial orders while enabled. Malformed,
missing or bounded-pagination-exhausted evidence fails closed; signed payloads alone
cannot credit balances. Adverse evidence records holds without reversing value.
Legacy batch reconciliation excludes financial orders; use the dedicated recovery
APIs to preserve the customer-first lock order.

Provider protocol references: [create order](https://razorpay.com/docs/api/orders/create/),
[order payments](https://razorpay.com/docs/api/orders/fetch-payments/),
[disputes](https://razorpay.com/docs/api/disputes/fetch-all/), and
[webhook validation](https://razorpay.com/docs/webhooks/validate-test/).

Remaining gates: Flutter voucher/reward screens and typed payment request integration;
separately authorized staging backup/isolated restore, migrations/deployment and TEST
provider/device acceptance; failed-attempt and late-expiry reconciliation policy;
referrals/reward expiry, hold release and balance adjustments. Legacy quantities,
bulk funding and Firestore financial balances are not imported by this phase.

Validation: 253 isolated API/ledger/payment/commerce/savings/custom-design tests
passed, plus 18 disposable PostgreSQL reservation/hold/funding concurrency tests.
Targeted Ruff and git diff --check passed. Provider calls were synthetic; these
results do not establish live provider, webhook or phone acceptance.
