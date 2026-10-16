# Saved-ledger financial reconciliation reporting

Owner approved this read-only scope on 2026-10-16 for local implementation/tests.
This is not a revenue, tax, profit, accounting-close or provider cash report.

## Guard and access

FINANCIAL_REPORTING_ENABLED defaults to 0. When enabled, APP_ENV must be staging
or test; production is rejected. There is no migration and no financial-write,
hold-release, refund, import or provider-fetch path. Existing tables are used.

| GET route suffix under /admin/financial-reconciliation/ | Permission |
| --- | --- |
| merchandise | support:manage |
| savings | savings:manage |
| custom_designs | support:manage |
| vouchers | vouchers:manage |
| rewards | rewards:manage |

Each route requires authentication and its existing domain permission.
metrics:read alone cannot expose financial records. Responses are no-store.
after is an ascending record-ID cursor; limit defaults to 20 and is capped at 50.
Returned next_after is the last listed ID when another page exists.

## Meaning and precision

Rows describe saved records only. There is no all-area total or client-supplied
calculation. Measures are nonnegative integers internally and decimal integer
strings on the wire so web clients do not round paise, micrograms or points.
The admin UI formats these with BigInt.

- Merchandise uses integer financial reservations, revalidates frozen quotes
  with the approved calculator and checks saved posting-event/order bindings.
  Legacy order snapshots without that evidence are counted separately as
  requiring reconciliation; their floating-point totals are not converted.
- Savings shows posted/pending principal separately and purchased/benefit gold
  in micrograms. Saved posting audits, plan identity, rate/bonus arithmetic,
  checkout bindings and closed-request resolutions are checked.
- Custom advances and balances stay separate. Existing accounting validation
  checks the immutable posting fingerprints/current quote; holds remain visible.
- Voucher paid funding, complimentary funding, redemptions and reservations stay
  separate. Saved funding events and payment-linked redemption evidence must
  match the ledger and reserved balance.
- Rewards show points issued, used and reserved separately, validating ledger
  and payment-posting evidence. Points are not converted to rupees here.

Voucher/reward accounts with over 1,000 entries require review rather than an
unbounded interactive read. Missing/inconsistent evidence produces null measures
and requires_review, never a guessed zero. Database failure returns generic 503.
A hold does not subtract a historical posting or authorize release.

as_of_ist is read completion time, not a financial close or a cross-page snapshot.
Concurrent changes can require a reload. Pending requests may already have a
provider capture awaiting verification; this report cannot authorize repayment.
provider_verified and release_authorized are always false. Responses omit
provider IDs, voucher codes, receipt reasons and customer contact/address data.

## Local validation and rollout boundary

108 focused reporting/financial/custom-balance/savings regression tests passed.
They include no-provider/no-DML reads, permission/environment guards, missing or
tampered audits, high-value integer precision, held history, cursor pagination,
distinct complimentary funding and safe unavailable responses. Posting evidence
reads are batched, and caches are scoped to a single request/account. A
20-posting reward account is verified in at most 12 SELECTs, avoiding one
database round-trip per ledger entry.

The separate admin entry point uses the default-off
SNCHATBOT_FINANCIAL_REPORTING_ENABLED build flag. Production, staging service
settings, active delivery routes, push sending, online custom balance creation
and voucher 2 were not changed. Staging rollout requires a separately reviewed
source/build checkpoint and explicit owner approval.
