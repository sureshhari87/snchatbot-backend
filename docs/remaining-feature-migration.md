# Remaining feature migration: staged implementation

This is not production deployment approval. No remote schema change or legacy
record import has been performed by this patch.

## Review API foundation

Revision `0015_product_reviews` adds review and helpful-vote tables. Text reviews start
pending; only approved reviews are public. Authenticated identity determines
ownership. Authors cannot publish, edit another author's review, or claim a
verified-purchase badge. Owner edits return a review to moderation. Admin
moderation requires `support:manage` and the exact reviewed `updatedAt` version.
Summary aggregates are calculated from approved rows, avoiding drift on edits.

Endpoints: GET/POST `/products/{id}/reviews`, GET suffixes `/summary` and `/me`,
PATCH/DELETE `/reviews/{id}`, GET `/admin/reviews`, and
PATCH `/admin/reviews/{id}/moderation`.

GET/PUT `/reviews/{id}/helpful` reads the authenticated caller's vote and sets an
explicit desired state. Repeating PUT does not increment a counter twice.
Authors cannot vote on their own review; unapproved reviews cannot receive votes.
Fit/size feedback and admin seller replies are implemented. Rejection requires a
reason; author edits reset moderation and clear the previous seller reply.
The latest additions still need a completed regression run before release.

This is deliberately not the complete legacy review contract. Photo ownership,
trusted delivered-order purchase verification,
historical identity/product mapping and the Flutter/admin adapters remain
unfinished. The app still uses its existing repository. Do not switch it until
these capabilities and source data have been migrated and tested. Do not relax
Firestore rules or infer purchase eligibility from client-supplied order data.

The staging launcher now requires revision 0015. Do not redeploy that launcher
against the current 0014 database: take a staging backup, verify the migration
on an isolated database and separately authorize/apply it to staging first.
The downgrade drops review records; it is not a data-preserving rollback.

## Notifications

FastAPI already stores notification preferences and a push token. Customer
inbox, seen state and admin broadcasts still use Firestore. Migrate inbox rows
with recipient ownership, admin-only broadcast creation and durable delivery.
Device association must follow backend login/logout and token rotation, with
cross-account isolation. FCM may remain the transport; Firebase login must not
be required. Never log device tokens. History import and opt-in/quiet-hours
policy need verification before switching reads/writes.

## Financial flows

On 2026-09-24 the owner confirmed that savings, reward balances and vouchers
contain test data only. No deletion or balance reset was requested or performed.
This simplifies reconciliation but does not authorize enabling unverified
financial writes or inventing business rules.

The legacy customer POST `/orders/sync` is now a compatibility lookup only:
it returns an existing owned server order and ignores all submitted state.
Unknown or another user's reference returns 409 without inserting anything.
It cannot create orders, mark payments paid, change amounts/items or overwrite
server provenance. Historical orders must use an audited operator import, not
client sync. Existing historical data still requires reconciliation before
granting verified-purchase badges or awarding financial benefits.

`RewardService` currently increments/decrements a Firestore user balance from
the client. Savings screens write payment records directly; voucher screens and
admin management also write Firestore. Do not copy these writes into generic
FastAPI endpoints or fabricate initial balances.

Required: source snapshot, customer identity mapping, balance/payment/expiry
reconciliation, integer minor-unit accounting, immutable ledger entries,
unique verified-payment references, transactional redemption and refund
reversals, idempotent webhook handling, and admin audit. Preserve existing
approved rules; ambiguous source balances require owner reconciliation.
FastAPI checkout must continue rejecting unsupported rewards/vouchers rather
than accepting a client-calculated discount.

## Admin and legacy

Admin login/role lookup, customer lists, dashboards, delivery configuration,
broadcasts, reviews, banners, savings/vouchers and customizations still have
Firestore paths. Migrate with authenticated server RBAC and audit, not client
`isAdmin` flags. Preserve unrelated `sona_admin/test_app` local changes.

Production remains gated on complete contracts/data reconciliation, passing
regression/security checks, fresh backup and isolated restore verification,
approved real delivery coverage and rollback evidence.
