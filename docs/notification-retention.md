# Customer notification retention

Approved 2026-10-23: read messages remain visible for 45 days from their first
read; unread messages for 90 days from creation. UTC cutoffs are inclusive.
Repeated mark-seen calls do not restart the read clock. Expired unread messages
cannot be revived by marking them seen.

NOTIFICATION_RETENTION_ENABLED=0 is the default. When enabled, the backend
filters expired records before sorting and pagination on each inbox request.
No scheduler, schema migration or client clock is required. Unread badges
derive from that same inbox response.

This removes customer inbox visibility, not stored notification rows. Retained
rows and deduplication keys prevent retries from recreating expired messages.
Admin broadcast audits, orders, payments, savings and push history are unchanged.
Permanent deletion or audit expiry is outside this policy.

Before enabling, an admin with support:manage can read:
GET /admin/notifications/retention-preview
The read-only preview reports expired read/unread counts even while disabled.
It returns no message content, recipients or credentials. Review counts and the
current database backup before activation.

Local implementation only. Staging/production deployment and configuration
remain unchanged. Production activation is part of release approval.

Local verification: 40 isolated SQLite regression tests passed, plus Ruff lint
and formatting. Inbox, broadcast audit/retry and order-notification tests are included.
