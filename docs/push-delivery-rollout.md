# Push delivery: disabled foundation

Owner-approved initial scope: verified-payment and order-status messages only;
interpret customer quiet hours in Asia/Kolkata. Promotional sends stay disabled.

## Implemented locally

`push_delivery_policy.py` is a pure, default-disabled decision function. It checks
recipient ownership of preferences, push and order-update opt-outs, and quiet
hours. Quiet intervals include the start and exclude the end, including windows
crossing midnight. Missing, partial, malformed or equal quiet-hour boundaries
block delivery (both null means no quiet interval). Missing preferences also
block delivery. Deferred decisions return a UTC retry time; the worker must
re-evaluate preferences when retrying, not reuse an earlier approval.

`push_outbox.py` now contains a default-off transactional enqueue function and
an injected-provider worker. Migration `0019_push_outbox` creates event/attempt
tables, with no backfill; nonempty downgrade is refused. Enqueue snapshots the
active session family and token digest once. Repeated event keys do not add
later logins. The queue holds no deliverable token or inbox content.

Worker claims lock the owner before the attempt, using PostgreSQL SKIP LOCKED.
Logout and token rotation use the same owner lock. The lock is held during the
provider call, so the adapter MUST have a short bounded timeout and a
dedicated low-concurrency worker; never run it inside a checkout request.
Session, token generation, account verification and preferences are rechecked.
Quiet hours defer without consuming attempts. Transient/ambiguous errors retry
at 30/60/120/240 seconds (longer for provider Retry-After); five failed calls end the attempt. Event lifetime is
24 hours. These are technical initial limits, not a delivery SLA.

Trusted new payment/status inbox events now enqueue in the same transaction,
behind `PUSH_OUTBOX_ENABLED=1` plus `PUSH_DEVICE_REGISTRATION_ENABLED=1`. Existing
inbox rows are not replayed when enabling, and arbitrary admin inbox messages
never enqueue push. Reconciliation uses a savepoint so queue failures cannot
partially commit its financial effects while recording a failed audit.

`fcm_sender.py` implements HTTP v1 using a dedicated service-account secret and
explicit project matching. OAuth refresh happens before database locks. There
is no default Firebase/ADC credential fallback. `run_configured_batch` additionally
requires `PUSH_DELIVERY_ENABLED=1`; no API route or scheduler invokes it. All three
flags must be set for delivery. Do not set them during this implementation phase.
Provider HTTP requests have five-second per-operation timeouts, no transport
retries or redirects. Authentication preparation has separate ten-second request
timeouts and happens outside database transactions. 401/403 stop the batch and
defer, without consuming an attempt; only explicit FCM UNREGISTERED errors remove
matching device registrations. Provider error bodies and credentials are never
persisted by this adapter.

Messages use generic title/body, user/event/type data, and an Android stable tag.
Android TTL and APNs expiration are zero: an offline device may miss the OS push;
its durable inbox remains available. This reduces stale queued OS messages but
cannot recall a notification already accepted/displayed. No payment amount,
order reference, customer address or inbox body goes into the push payload.

Owner lifecycle and worker claims use PostgreSQL NO KEY UPDATE (SKIP LOCKED for
the worker), allowing existing foreign-key KEY SHARE without lock-upgrade cycles.
Preference PATCH uses the same owner lock through its single commit, including
when default preferences are newly created.

Passing `enabled=True` in unit tests does not activate any environment. The
local staging launcher now requires 0019 and all three push tables. DO NOT deploy this batch yet: adding an
Alembic head can trigger startup migrations independently of a sending flag.

## Still required before enabling

Staging-only operator controls are now implemented locally:
`python -m scripts.staging_push_worker` is read-only by default. It refuses local
`.env` files and requires explicit environment configuration, `APP_ENV=staging`,
`STAGING_NEON_HOST` matching the engine host, actual database/role
`snchatbot_staging`/`staging_owner`, and exact revision `0019_push_outbox`.
`PUSH_STAGING_ALLOWED_USER_IDS` must contain 1-5 positive integer IDs, comma-separated
without spaces. Confirm these are consenting staging testers before configuration.
Queue selection and claims exclude other recipients without mutating their rows;
the adapter rechecks staging environment and recipient membership before sending.

Only after migration/deployment approval: `--send --limit 1` requires typing
`SEND_STAGING_PUSH`. All existing delivery flags remain required. Maximum batch
size is five; no automatic loop, API endpoint or scheduler is installed. The
read-only mode loads no FCM credentials. Sending checks database identity before
OAuth preparation. Output is counts/outcomes only; provider acceptance is not
proof of phone delivery. No environment variables were activated by this patch.

1. Complete regression/concurrency verification of integrated producers, adapter,
   opt-outs and rollback handling; review registration freshness/lease policy.
2. Review/test the new manual staging-only worker on approved staging recipients;
   configure least-privilege credentials later. Monitoring/scheduling remain pending.
3. Flutter order/inbox tap routing is implemented locally; device regression remains. Existing foreground
   ownership checking remains; OS auto-display is not controlled by that check.
4. External delivery cannot be exactly-once across provider acceptance and worker
   crashes. Stable notification tags are not proof of exactly-once delivery.
5. Device tests for foreground/background, denied permission, offline logout,
   account switching, quiet hours, opt-out, retries and invalid-token cleanup.
   Already accepted OS notifications cannot be recalled by server-side logout.
6. New migration backup/isolated restore rehearsal and explicit staging rollout
   before any production consideration. No FCM credentials are needed yet.

Credential names for later setup (do not paste values into chat or Git):
`PUSH_FCM_PROJECT_ID`, `PUSH_FCM_SERVICE_ACCOUNT_JSON`. No credentials have been
configured and no real FCM sends have been performed by this implementation.

References: https://firebase.google.com/docs/cloud-messaging/send/v1-api ,
https://firebase.google.com/docs/cloud-messaging/error-codes ,
https://www.postgresql.org/docs/current/explicit-locking.html .

## Local verification

Operator-reported isolated restore drill passed on 2026-10-02 from backup
`20261002T100655572398Z` (SHA-256
`352ea9f28396da9ba79bc125f37f1d361d6c591b2595fe65bf03bb8a4eb20149`).
The local copy upgraded from 0018 to 0019, preserved 5 users, 24 products,
6 notifications and device registrations, and had empty outbox tables. The local
server stopped successfully. Staging and production were not modified. This is
working-tree rehearsal evidence, not approval to deploy commit 72af18f or head.
The launcher revision guard is updated locally; remote migration is still separate.

Launcher/control regression after the guard update: 45 tests and 31 subtests
passed (16 dependency warnings), exit code 0. Report:
`.pytest_cache_local/staging_revision_0019.xml`. Scoped Ruff and diff checks passed.
All 14 source hashes listed in the restore report matched the current files when
checked on 2026-10-02. The separately tested launcher change is not among those
hashed migration/delivery sources. No remote migration or push activation occurred.

Staging-control integration: 60 targeted backend tests passed (16 dependency
deprecation warnings), including read-only database/role/host/TLS/revision checks,
allowlist validation/filtering, default dry-run and cancelled-send behavior,
runtime recipient rejection, adapter, queue, migration and producer regression.
Report: `.pytest_cache_local/staging_push_controls.xml` (local only).
Scoped Ruff, Bandit and diff whitespace checks passed. Separate synthetic restore
manifest validation passed; backup/restore helpers compile. These are NOT evidence
of a fresh real staging backup, restored customer archive, or actual FCM delivery.

Expanded integrated run: 123 passed, 3 failed (16 warnings). All three failures
were the same module-scoped disposable PostgreSQL fixture: `initdb` exceeded
180 seconds before the worker/logout/rotation assertions could run. This is not
a clean regression result and must not be treated as deployment approval.
Report: `.pytest_cache_local/push_delivery_verification.xml` (local only).
A focused retry is required; do not weaken concurrency assertions to bypass
database setup failures. Provider calls remain mocked throughout these tests.

Focused retry completed: 3 passed, 16 warnings in 137.20 seconds, exit code 0.
Report: `.pytest_cache_local/push_outbox_retry.xml` (local only). No application
code or concurrency assertions were changed to obtain this result. All 126
selected cases therefore have passing evidence across the initial run and retry,
not a single clean full run. The warnings concern dependency deprecations.
This does not verify actual FCM delivery or authorize staging migration/deployment.

Policy, queue, isolated SQLite migration, existing device registration and
deferred logout regression: 43 tests passed. Three additional tests passed on a
fresh disposable loopback PostgreSQL cluster: competing worker skips the locked
owner, logout-first suppresses delivery, and token-rotation-first suppresses the
old queued token. Sender calls are mocks; no FCM request or Neon write was made.
This is not a staging backup/restore drill or end-to-end delivery approval.
