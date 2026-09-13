# Catalogue import and administration cutover

Schema `0014_catalogue_source` preserves Firestore document IDs in a unique
`products.source_id` column and the original fields in `source_data`. Public
product responses expose a limited set of image/size/readiness attributes,
not the entire source document. Cloudinary image URLs are retained.

`catalogue_data.import_snapshot(db, snapshot)` is insert-only and never commits
on its own. Use a caller-owned transaction. Existing source IDs are skipped;
repeating the import does not reset stock after purchases or overwrite later
admin edits. It is not a synchronization tool. Invalid prices abort the import;
missing stock makes a product unavailable. Empty documents are reported and
skipped. Source snapshots and credentials must not be committed.

Dynamic prices are derived from saved `gold_rates/today` data, stored privately
under `commerce.metal_rates`. Positive validated rates and price inputs are
required. Admin rate updates recalculate dynamic product prices in the same
transaction without changing stock. A final write freeze and new source export
are required before cutover so the rates and inventory are current.

## API

- `GET/POST /admin/catalogue/products`
- `PATCH /admin/catalogue/products/{numeric_id}`
- `GET/PUT /admin/catalogue/rates`
- `GET /catalogue/metal-rates` (only public metal rates)

Admin endpoints require the existing `products:manage` permission. Existing
backend product deletion remains available to authorized administrators; imports
do not automatically delete existing products or demo rows. Imported sizes are
checked against saved options. Alternate purity/colour pricing is not supported
as a public selectable option yet.

## Rehearsal evidence

The read-only source snapshot contained 22 documents. In-memory import inserted
20 products, skipped two empty documents and left five products unavailable.
All computed selling prices were positive. A repeat import inserted no duplicates
and preserved a simulated stock decrement and the source-to-backend mappings.

Fresh Neon backup `20260912T201619Z` passed checksum verification and a separate
local PostgreSQL restore. Schema upgrade from `0012_firebase_commerce_identity`
to `0014_catalogue_source` preserved counts (zero users, four existing products).
The local PostgreSQL server shut down successfully. Production was not modified.

The catalogue/admin/commerce targeted suite passed 14 tests. The full local
backend suite passed 283 tests (five skipped) with 80.38% coverage, meeting the
unchanged 80% gate. Lint, formatting, Bandit and secret-hygiene checks passed.
Pytest's optional debugger plugin was disabled for the local run after its
Python 3.14 import stalled; no tests or coverage requirements were disabled.
These checks do not
replace full release CI, isolated staging smoke tests, or an operator-controlled
production import. Delivery rules are deliberately deferred: do not seed the
test PIN prefix as real coverage. Checkout remains blocked until approved
delivery coverage is configured. User and order history have not been migrated.
