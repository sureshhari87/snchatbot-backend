# Bulk catalogue import

Local implementation; not deployed or enabled. No catalogue or image import has
been run against staging or production. No new migration is introduced; the existing migrated catalogue/audit schema is required.

## Prepare files

Use docs/templates/catalogue-import.csv in the Flutter repository. This file is
headers only: enter real approved product values; it contains no sample stock or
prices. Excel users should export UTF-8 comma-separated CSV. Native XLSX and
arbitrary JSON are not accepted in this first version.

Use a unique SKU containing ASCII letters, digits, hyphens or underscores (up to
64 characters). Prefer uppercase SKUs. Keep SKU, HUID and size cells as text in Excel. Names are required. Categories are Gold,
Diamond, Silver, Platinum and Gifts. Supply category, stock, pricing mode and
readiness explicitly. Zero stock is allowed; fractional or negative stock is not.

Gold and Diamond use gold metal with 18K/22K purity and Yellow/Rose gold colour.
Silver uses silver metal with 800/925 purity; Platinum uses platinum and PT950.
An HUID, when present, must be six letters/numbers. No hallmark is fabricated.
Gifts require fixed pricing. Other categories retain the existing admin choice
of fixed or dynamic pricing. Positive actual weight is required for all jewellery.
For dynamic jewellery supply
saved metal rates, and explicit making/stone/diamond/shipping charges, wastage
and positive GST. Monetary values are in rupees. Selling prices are calculated
by the existing backend pricing engine, never trusted from a dynamic CSV price.

Readiness fields match the editor: readyToShip and madeToOrder must be opposite
booleans, with handlingDays/manufacturingDays/sizeAdjustmentDays from 0 to 365.
Sizes and sizeAdjustmentSizes use | separators. Positive adjustment time needs
explicit affected sizes drawn from sizes. dispatchNotBefore is optional; use
YYYY-MM-DD. Missing/conflicting readiness blocks the batch.

image1 is required; image2-image4 are optional. Use either an existing public
HTTPS URL or flat ZIP filenames matching the SKU, for example:
GOLD-RING-001_1.jpg, GOLD-RING-001_2.png
Names and case must match exactly. Do not include directories or unrelated
photos. JPEG, PNG and WebP are accepted; encrypted ZIPs, symbolic links, SVG,
animated images and invalid image/extension combinations are rejected.

Limits: 50 products; CSV 512 KB; ZIP 20 MB compressed / 24 MB expanded;
200 photos, each at most 8 MB and 12 megapixels; 100 megapixels across the ZIP.
Split larger catalogues into smaller batches. The enabled import preview/confirmation routes accept up to 30 MB of JSON, including base64 photos; all other endpoints retain their configured global request limit. Import authentication is required before the route consumes the bundle. Both declared and streamed request sizes are bounded. Originals are preserved; the
preview uses small re-encoded thumbnails to limit client memory use.

## Admin workflow

Product Management -> Bulk import products:
1. Copy the CSV header or use the checked-in template; choose CSV and optional ZIP.
2. Validate and preview. This creates no database records and uploads no photos.
3. Review photos, specifications, server prices, stock and delivery readiness.
4. Provide an audit reason, confirm the review checkbox, then confirm import.
5. ZIP photos upload sequentially to the existing image service. The backend
   creates all product rows, per-product audits and the batch receipt atomically.
6. All created products are inactive, regardless of input. They are excluded
   from public catalogue discovery/direct product links. Open each draft in
   Product Management and review it before turning Visible to customers on.

Duplicate existing SKUs abort the entire batch. This is create-only: existing
products, stock, prices and sample records are never overwritten or deleted.

The preview expires after 15 minutes and is signed against the files, admin
account, environment and saved metal rates. Any change requires preview again.
Repeated successful confirmations return the original product IDs without
resetting later admin edits or stock changes.

## Recovery

Keep the import key for recovery. After an uncertain confirmation, Read saved
status is mandatory before retrying or replacing files. Only the originating
admin account can read that key. The same action is available after reopening
the screen by pasting the key. No automatic mutation replay occurs.

Completed photo uploads are reused while the screen remains open. If upload or
database confirmation fails, external image objects may already exist; they are
not automatically deleted. Keep the window open to reuse them. Closing/reloading
loses that client upload cache. Check saved batch status before starting again.
There is no automatic image-provider cleanup or durable upload-job scheduler.

Session/account changes clear the files, preview and upload cache; late results
cannot confirm an import into the new session.

## API and activation

Permission: products:manage. Routes:
POST /admin/catalogue/imports/preview
POST /admin/catalogue/imports/confirm
GET /admin/catalogue/imports/{request_key}

CATALOGUE_BULK_IMPORT_ENABLED defaults to 0. Preview/confirm remain unavailable
until enabled; saved status remains readable to the authorized originating
account. Activation/deployment needs a reviewed staging rollout, backup/restore
verification and synthetic admin/file-picker checks before production approval.

A private transactional import lock and batch receipts reuse app_config_entries; immutable per-product audits
reuse catalogue_content_audit. No new tables or migration are introduced.
Provider credentials or database URLs never belong in CSV/ZIP files.

File selection uses the pinned file_picker package and archive ZIP decoder:
https://pub.dev/packages/file_picker/versions/10.3.10
https://pub.dev/packages/archive/versions/4.0.9

## Verification state

Local verification completed:
- Broader backend regression: 128 passed; 5 live-integration checks skipped.
- Final bulk-import follow-up: 42 passed, including three real disposable
  loopback PostgreSQL races (same key, duplicate SKU and case-variant SKU).
- Admin: 12 widget/readiness tests passed; targeted analysis found no issues.
- Ruff lint/format passed. Unrelated local edits retained their recorded hashes.
- CSV template headers match the backend and admin client exactly.

Source publication/CI, matched staging rollout, real file-picker/image-provider
verification and production release approval remain pending. No real catalogue
data was imported, no provider images uploaded, and no schema migration ran.
Native macOS/iOS builds have not been verified.
