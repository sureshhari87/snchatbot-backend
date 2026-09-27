# Automatic inbox notifications (local implementation)

- Admin order updates to processing, shipped, delivered or cancelled enqueue
  one inbox message per order/status, attributed to the acting administrator.
- Successful Razorpay finalization enqueues one payment confirmation per order,
  after provider capture confirmation, amount/currency and inventory checks.
- Payment messages use a NULL created_by to identify the system sender.
  Existing human-authored messages keep their sender. Public API payloads do not
  expose sender or recipient IDs.
- Payment deduplication uses the database recipient/key unique constraint with
  conflict-ignore inserts, not webhook event IDs. No historical backfill occurs
  on the already-finalized early-return path.
- Both types are written in the caller's database transaction. This is an in-app
  inbox feature, not SMS, FCM push, email or a delivery guarantee.
- Address-only edits, payment failures, pending/authorized payments, refund
  events and customer support requests do not create these messages.

## Release gate

The local launcher now requires 0017_system_notifications. Render currently
runs 0016; do not deploy this change before a staging backup, isolated restore/
migration rehearsal and controlled staging upgrade to 0017. Existing 0016
operator helpers must not be reused by simply changing their revision strings.

0017 makes created_by nullable. Downgrade refuses to run if system messages
exist: it does not delete messages or invent a human author to force rollback.
Production migration and deployment remain separately gated.
