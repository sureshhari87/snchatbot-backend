# Release source reconciliation — 5 November 2026

This is a read-only source audit and preparation checkpoint. It does not approve or perform a merge, deployment, migration, financial action or production activation.

## Verified staging state

- Branded APK source: `0a484213f3cb91956c8ce1c73dae82ff9687c461`; CI-only child `99441ae536c6914ba763c8685117e5126a41d9af` has identical runtime. Startup analysis, three startup tests, package/signature/native alignment and owner logo/splash/Home checks passed.
- Live staging backend: `f79075a7aa6fb43f6150dd7b6dbe6ce8d21f398c`, schema `0041_customer_profiles`. Fresh backup and isolated restore passed; final saved-record and AI-configuration checks passed. No migrations ran.
- Filtering fixes: review backend `cb211bba43ce85eb0e1626b4ad9f0615478a7775`. Sixty-six local targeted tests and all reported CI jobs passed. These fixes are not deployed.
- Live AI quality failed: rules/template fallbacks and no generated Studio image. A controlled probe returned HTTP 429 / `credit_balance_exhausted`. Owner deferred restoring API credits. No further generation is authorized by this checkpoint; retain the existing bounded request journal. Model metadata success is not a generation pass.

## Source gap requiring reconciliation

The branded AI test build is a scoped candidate, not a complete release build. Direct commit-to-commit comparisons confirmed completed all-updates work is missing from that narrower source:

- Backend unread-notification count endpoint and its tests.
- Catalogue-update restock notification trigger and portions of the stock-alert inbox workflow.
- Optional product-video validation/API metadata and tests.
- Customer restock-product navigation, bottom wishlist badge, richer photo zoom controls and product-video presentation/tests.
- Home badge, legal/privacy and order formatting differences also need deliberate reconciliation. A difference alone is not classified as a regression.

Use the all-updates commits as preservation references, then reconcile AI, owner branding and the tested filtering fixes in an isolated combined review candidate. Do not replace completed features with older implementations. Verify shared session/auth/request code together and require combined CI and a matching staged artifact; separate branch successes do not prove combined release readiness.

| Repository | All-updates reference | AI/follow-up review | Divergent paths changed on both sides |
| --- | --- | --- | --- |
| app | `72565c9` | `99441ae` | 16 |
| backend | `5c64145` | `cb211bb` | 4 |

Identical changes independently present on both sides are excluded from the lists below. These are source-reconciliation candidates, not proven textual conflicts.

### App divergent shared paths

- `.github/workflows/billing-documents.yml`
- `assets/fonts/LICENSE-NotoSans.txt`
- `assets/fonts/LICENSE-NotoSansTamil.txt`
- `docs/app-branding-artwork.md`
- `docs/sona-customer-terms-review.md`
- `lib/screens/order_screen.dart`
- `lib/screens/product_details.dart`
- `lib/screens/product_photo_viewer.dart`
- `lib/screens/profile_screen.dart`
- `lib/screens/sona_ai_studio_screen.dart`
- `lib/services/snchatbot_api.dart`
- `lib/widgets/app_startup.dart`
- `pubspec.yaml`
- `sona_admin/lib/screens/order_details_screen.dart`
- `sona_admin/lib/screens/product_management_screen.dart`
- `test/sona_ai_studio_screen_test.dart`

### Backend divergent shared paths

- `catalogue_data.py`
- `main.py`
- `sona_ai/main.py`
- `tests/test_customer_flows.py`

## Remaining release work

1. Prepare and verify one combined release source; complete matched staging checks for its non-AI features without consuming generation quota.
2. After owner restores API credits, resume only the remaining bounded AI evaluation and account-isolation/phone cases. No fallback or template is a live AI pass.
3. Real catalogue/photos/HUID/weights/stock/readiness; courier jewellery insurance, serviceable PINs, calendar/cutoffs and coverage approval. Active routes remain unchanged.
4. Accountant e-invoice applicability and tax/rounding decisions, electronic signing arrangements, final policy/contact and high-value identity operations. Existing issuance/refund/identity controls stay unchanged.
5. Verified external-HDD recovery; final production data/configuration reconciliation, release signing/store/app-link checks, write-freeze backup and isolated restore, rollback plan and explicit production approval.

Production remains on the existing Hugging Face + Neon configuration. Begin any production rollout in the owner-requested new chat. Preserve legacy/test records and voucher 2; no deletion, reset or new charge is implied.

## Isolated combined candidate prepared

Local directories: `.codex/release-integration-preparation-20261105/app` and `backend`. These start from committed all-updates baselines, retain completed features, and selectively add reviewed AI/branding/filtering changes. Fifteen preservation checks passed, including unread counts, restock hooks/locks, video metadata, badges, legal screens and richer photo controls. Existing branches and normal indexes were not changed. No merge or deployment occurred.

Combined backend verification covered 110 unique targeted cases. The initial suite passed 109 and rejected one obsolete fake-image fixture; after replacing that fixture with a real tiny synthetic PNG, all ten tests in the affected customer-flow module passed. The secure validator was not weakened. Lint passed. Providers were mocked or disabled, with zero generation calls.

Combined app analysis/tests, frozen review commits, remote CI and a matching APK remain pending. This candidate is unpublished and undeployed. Update its source manifest to the eventual frozen backend commit before building a matched APK. This checkpoint is not release approval.
