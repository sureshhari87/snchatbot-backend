# Verified review purchase attribution

Owner approved the matching delivered, verified-paid merchandise order rule on
2026-10-18. This is local implementation only; no rollout is authorized here.

New reviews receive a server-selected order link only when an owned SQL product
item has positive quantity in a delivered/completed Razorpay checkout order with
verified payment, a matching posted financial reservation/payment reference and
an immutable payment_posted event. Legacy payment-status strings alone cannot
establish attribution. Existing reviews retain NULL attribution, including when
edited; historical attribution requires separate reconciliation.

Every review response rechecks the bound order. Refund/return/pending states,
missing payment evidence, ownership mismatches or reward-account/used-voucher
holds suppress the badge. The check never calls a provider or changes balances,
holds, review moderation or order state. Public responses never expose the order
link. API input rejects client-supplied attribution fields.

Migration 0035_review_purchase adds a nullable named foreign key and preserves
existing reviews. Staging remains at 0034; deployment requires a separately
reviewed source and backup/restore rollout. Review-photo scope is still awaiting
an owner decision. Verified-only filtering is implemented in FastAPI and Flutter;
it applies the same evidence predicate before pagination. The Flutter repository
fails safely if an older server returns unverified rows for this filter.
The new staging launcher requires schema 0035 and the attribution column,
regardless of other feature flags; it never applies migrations on startup.


Local validation on 2026-10-18: 25 review/order-security tests passed; 59 mocked
startup/feature tests and 14 subtests passed. Ruff lint/format and Bandit for the
review modules passed, and the repository source-hygiene check passed. The
migration round trip was tested on disposable in-memory SQLite only; PostgreSQL
backup/restore and migration rehearsal remain staging rollout gates. The
PostgreSQL eligibility SQL was compiled and its nested hold correlation reviewed;
an unrelated-account hold regression passes. No live database was migrated.
