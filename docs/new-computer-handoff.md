# New computer staging handoff - 2026-09-14

Use branch `codex/fastapi-commerce-migration` in BOTH GitHub repositories:
`sureshhari87/snchatbot-backend` and `sureshhari87/sona_jewellery_app`.
This is unfinished staging work, not a production release. Do not merge/deploy
production just to move computers.

## Windows setup

Install Git and Python 3.12. In a parent folder with neither clone present:

```powershell
git clone --branch codex/fastapi-commerce-migration https://github.com/sureshhari87/snchatbot-backend.git
git clone --branch codex/fastapi-commerce-migration https://github.com/sureshhari87/sona_jewellery_app.git
cd snchatbot-backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest tests.test_local_staging -v
.\.venv\Scripts\python.exe scripts/local_staging.py --test-payments --firebase-auth
```

Use the hidden prompts for the DIRECT/non-pooled TLS URL: Neon project
`snchatbot-production`, branch `staging`, database `snchatbot_staging`, role
`staging_owner`. Confirm the branch in Neon. Type `START_STAGING`, then enter
the Razorpay TEST Key ID (`rzp_test_...`) and matching secret. Keep the terminal
open. Keys are in memory only and must be re-entered on restart.
Do not copy a production .env into staging. Stop the old staging launcher first.

The staging database is already remote and populated: moving computers does
NOT require creating another database, importing again, resetting or stamping.

## Verified state and blockers

- Import receipt: schema `0014_catalogue_source`; 20 inserted, 2 empty source
  documents skipped, 5 unavailable, 24 total including 4 pre-existing products.
  Do not silently replenish stock or delete the pre-existing records.
- Snapshot SHA256: `b1ef8e2c5abe11d0f6116f8fa564add25b2c1032304770e4172d88505252d5a5`.
- Earlier readiness/API smoke checks passed. Returned catalogue prices were
  positive. Image URLs were preserved; network image checks were inconclusive
  because of a local TLS certificate verification error.
- Latest restart passed schema verification but raised RuntimeError before
  STAGING READY. Backend exit vs readiness timeout remains to be diagnosed.
- Firebase public token verification uses `sona-jewellery-app`; Firestore
  commerce is disabled. Users/permissions are in staging SQL. No staging admin
  identity has been approved or granted. Actual staging login remains unverified.
- Razorpay test keys previously loaded, but provider acceptance, actual test
  payments and payment webhooks have NOT been verified.
- Latest export contains zero inventory_locations, product_inventory and
  delivery_sla_rules. Obtain approved PIN codes and handling/transit/working-day
  rules. Never use fixture delivery coverage as a real business promise.
- New staging APK build has not been verified successful or installed. Old
  September 10 APK is not a staging artifact. Product-unavailable checkout error
  happens before Razorpay: first verify installed build URL and FastAPI IDs.
- Production schema/import/deployment remain untouched by this workflow.

## Private files and release gates

GitHub intentionally excludes database URLs, payment keys, .env, service-account
files, token caches, Firestore exports, database backups and signing keystores.
Recover secrets from dashboards and authenticate on the new computer as needed.
If private files are required, transfer them by a trusted encrypted channel,
not GitHub. Do not copy the whole .codex folder or old access-token caches.
Private source snapshot remains on the old computer in the app workspace at
`.codex/firestore-snapshots/snapshot-tz4RsI/commerce.json`; it is not needed just
to use the existing staging database.

Read the app repository's `docs/new-computer-handoff.md` for Android setup and
signing constraints. Full CI, admin analysis, stock/image review, approved
delivery, login/permissions, payment success/failure, webhook signature/replay,
order creation and stock checks remain pending. User/order-history migration
has not been completed. After staging acceptance, freeze source writes and
re-export; take and restore-verify a NEW production backup before migration.
September 12 backup/restore evidence is historical, not a fresh release backup.
