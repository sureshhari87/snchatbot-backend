# Session-owned push registration: disabled pending staging review

Revision `0018_push_devices` adds a separate registry. Existing notification
preferences and inbox records are unchanged. No legacy push tokens are imported.
Nonempty downgrade is refused; preserve records before any rollback plan.

`PUSH_DEVICE_REGISTRATION_ENABLED` defaults to `0`. Keep it disabled in Render
and production. Rehearse the migration on an isolated restored backup and apply
it separately before enabling the flag. Startup migrations may apply new Alembic
heads on deployment even with this flag disabled: do not deploy before review.

## Authenticated endpoints

- PUT `/users/me/push-device`: body contains `refresh_token` and `push_token`.
- POST `/users/me/push-device/detach`: body contains `refresh_token`.

Both require a verified backend customer and bearer authentication, have rate
limits, and return no tokens or customer identifiers. User identity is never
accepted from request data. Registration requires an active stored refresh
token for that customer. Token conflicts are rejected, not silently transferred.
Same-family registration is idempotent and replaces the previous FCM token.
Each session family represents one installation; separate logins support
multiple devices. Detachment is scoped to both customer and session family.

## Logout and concurrency

When enabled, registration, refresh, logout and user-wide revocation acquire
the same owner-row lock first. Single logout revokes the entire matching family
(including a replacement token created by a racing refresh) and deletes its
device row. Logout-all, password reset and detected refresh reuse remove every
registration for that customer. Normal refresh keeps the registration intact.
Eligibility excludes families without an active unexpired refresh token.

Real PostgreSQL tests use a fresh loopback-only cluster with synthetic data.
They exercise both registration-first and logout-first ordering for single and
all-device logout, including observed database lock contention. No Neon URL is
used. Tests call the actual logout handlers and registry service; HTTP auth,
validation and feature-gating are separately tested through TestClient.

## Still required before push delivery

- Flutter integration, offline logout cleanup and explicit account switching.
- A durable sender with delivery-time ownership revalidation, preferences,
  quiet hours, retries, invalid-token cleanup and registration lease policy.
- Device tokens are stored as deliverable plaintext in the restricted database;
  do not log/export them. Assess encryption/access controls before production.
- On-device staging tests after backup/restore and migration approval.

This change does not send FCM messages, enable staging, or migrate production.
