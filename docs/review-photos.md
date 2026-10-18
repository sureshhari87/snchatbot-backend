# Secure review photos: local candidate

Owner approval: include secure review photos, 2026-10-18. This is local
implementation only; no staging or production rollout is authorized here.

## Storage and validation

FastAPI stores normalized JPEG bytes in private PostgreSQL/SQLite media rows;
no external media credentials or unsigned Cloudinary uploads are used. Backups
include private media and must stay outside Git. Source JPEG/PNG/WebP uploads
are limited to 512 KB inside the existing 1 MB request limit. Images must decode,
be non-animated and at most 16 million pixels. A fresh image applies orientation,
removes original metadata/profiles/trailing bytes, flattens alpha on white and
writes JPEG at most 1280 by 1280 pixels / 384 KB. See upstream
[Pillow image APIs](https://pillow.readthedocs.io/en/stable/reference/Image.html)
and [orientation handling](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html).

Initial operating bounds: five photos/review, 20 uploads/account/24 hours,
20 MB stored/account and ten upload requests/minute/IP. Owner row locks protect
quota checks and binding. Stable upload keys permit safe duplicate/auth-renewal
retries; changed data/product or removed keys are rejected. Database constraints
verify stored byte lengths. Original filenames are never sent or stored. A non-waiting semaphore permits
one decoder per worker, bounding peak image memory; busy requests fail safely
and can retry with the same upload key.

## Privacy and moderation

Draft previews are authenticated and owner-only. Administrators with
support:manage can preview only photos attached to submitted reviews. Private
bytes use authenticated, non-cacheable JSON, with no bearer credentials in URLs.
Public image reads require a currently approved review, matching owner/product
and a live photo reference, and send no-store/nosniff. Arbitrary external image
URLs cannot be submitted as photo references. Edits return the review to pending
and revoke public image responses. Older clients omitting photo_ids preserve
attachments. Explicit removal erases bytes and retains a minimal tombstone for
idempotency and daily-limit accounting. There is no automatic legacy/data reset.

Unsubmitted drafts remain private and quota-bounded until explicitly removed.
Automatic retention/cleanup is not implemented; owners can recover and remove
unsubmitted uploads through the private draft controls. Owners can recover uncertain upload IDs
through a private product/key lookup before deletion. The phone form retains text
on upload failure, retries the same key, supports private previews/removal and
clears previews when the account changes. Admin approval requires photo loading,
actual decoding and acknowledgement before the existing version-checked write.

## Rollout gates

REVIEW_PHOTOS_ENABLED and SNCHATBOT_REVIEW_PHOTOS_ENABLED default off. Migration
0036_review_photos follows the unapplied 0035_review_purchase. The candidate
staging launcher requires both schemas and does not migrate automatically.
Live staging remains backend a3ad87c, schema 0034, customer APK 191879d.
A separate staging approval must cover fresh backup/isolated restore, both
migrations, matched app/admin builds and synthetic moderation/device tests.
No provider request, payment or voucher-2 action belongs to this feature.
Positive purchase-badge acceptance still requires genuine saved payment evidence.

## Backend validation - 2026-10-18

The broad review/schema suite passed 102 tests and 14 subtests. The final
photo/draft suite passed 17 tests, including two additional decoder resource
cases (15 overlap the broad run). Three real PostgreSQL tests passed on the
final source: duplicate-key races, quota locking and migration 0035/0036 round
trips preserving legacy review content. The disposable server stopped cleanly;
no external database or provider was used.

Ruff, format checks, Bandit on the review modules, credential hygiene and the
single Alembic head check passed. Migration 0036 is prepared but unapplied to
staging. Client/admin validation is recorded in the app repository. These local
checks do not authorize deployment or establish staging phone acceptance.
