# Custom designs (default disabled)

Set CUSTOM_DESIGNS_ENABLED=true only for a reviewed rollout after applying
0025_custom_design_quotes and 0026_custom_design_advances. Disabled routes return
503 before touching new tables. Existing generic custom-order contracts remain.

Customer routes: GET /custom-designs/my and POST /custom-designs; POST /custom-designs/{id}/decision;
POST /custom-designs/{id}/checkout, /verify, /refresh;
POST /custom-designs/{id}/cancellation-review. Ownership comes from auth.
Administrator routes under /admin/custom-designs expose list, quotes, audit,
manufacturing/start, holds, reconciliation and reconcile-checkout.
Administrators require support:manage, matching existing custom-order access.

Quotes are immutable versions; decisions reference the current version. New
versions require fresh acceptance. Checkout locks financial terms. Money is
strict integer paise; total >0, advance 0..total, nonzero advance >=100 paise.
No balance, quote status, paid status or customer identity is client-owned.

Checkout commits durable intent before contacting Razorpay. Unknown outcomes
cannot issue another order. Recovery validates provider receipt, quote notes,
amount/currency and the exact saved payment. Verified capture posts one immutable
credit and audit. PostgreSQL row locks plus unique constraints serialize races.

The separate signed endpoint is /custom-designs/payments/razorpay/webhook.
It uses configured Razorpay credentials and webhook secret; staging requires
dedicated TEST settings and a separately configured enabled endpoint.
Capture/order-paid events recheck provider capture. Refund/dispute events verify
fresh provider evidence and hold affected designs. Duplicate capture cannot add
credit. Raw bodies/signatures/provider responses must never be logged.

Manufacturing requires current acceptance, verified required advance and no hold.
Admin start requires an audited reason/idempotency key. Completion and remaining
balance collection are disabled pending business policy. Paid cancellation
requests create holds only. No automatic refunds, adjustments or hold release.

Read-only reconciliation verifies saved quote/checkout/credit/audit consistency;
it is not fresh provider evidence and does not authorize release. Quotes, decisions,
audits, advances and holds have database immutability triggers. Downgrade refuses
to discard any saved custom history. Never reset or import Firebase balances.

Local verification: 222 financial/custom/mobile tests passed; four actual
disposable loopback PostgreSQL concurrency cases passed. Provider webhook,
device/image acceptance, legacy data reconciliation and rollout remain pending.
Production remains unchanged.
