# Owner-approved staging test PIN: 625009

The owner approved this exact PIN for staging after the inactive timing draft.
This is NOT courier verification or production serviceability approval.

- Standard delivery only; PIN `625009` (not prefix `625`).
- 2 handling + 5-13 transit business days; Monday-Saturday; 16:00 IST cutoff.
- Holiday list is empty for this staging fixture, not an approved production calendar.
- Test with ordinary in-stock products, not customised/made-to-order products.

## Apply once, with hidden credentials

On the current computer:

```powershell
cd C:\Users\sures\sona_jewellery_app\.codex\fastapi-commerce
& "C:\Users\sures\snchatbot-staging\.venv\Scripts\python.exe" scripts/configure_staging_delivery.py
```

On another computer, first obtain the committed helper from the backend migration branch
without overwriting local work, then run it with the existing backend virtual environment.

At the prompts:
1. Copy only `STAGING_NEON_HOST` from Render.
2. In Neon select project `snchatbot-production`, branch **staging**, database
   `snchatbot_staging`, role `staging_owner`; copy its DIRECT TLS URL into the hidden prompt.
3. Type `CONFIGURE_625009`.

The helper refuses non-staging role/database names, a different host, an unexpected schema,
public config entries, or existing nonmatching delivery rules. It creates a private
`commerce.delivery_routes` entry only if missing, or populates an existing empty array.
Running it again with the exact same route makes no change. It never resets inventory,
changes schema, sends SMS, creates a payment, or configures production.

Do not paste the URL/password in chat or save it in command history. If setup fails,
share only the fixed error label. Successful output contains `delivery: configured`
or `already_configured`. No Render restart is required; delivery rules are read from SQL.

## Verify in the app

Use the Render-staging build. Pick an available imported product, choose an available
size if required, and use a complete address with PIN 625009. Confirm delivery is offered.
Check a neighbouring PIN such as 625008 is refused. Stock and payment validation stay enabled.
Do not select rewards/coupons/vouchers; those FastAPI flows are not yet configured.

Once delivery is confirmed, tap Pay Now and confirm Razorpay TEST checkout opens.
Cancel first, verifying cart/details survive; then perform a provider test success.
Check exactly one paid order, one stock deduction and appropriate cart cleanup.
An SDK success callback alone is not proof of server verification.

## Configure test webhooks separately

Generate a dedicated random webhook secret of at least 32 characters in your password
manager. This is NOT your Razorpay API secret or login secret.
Save it in Render Environment as `RAZORPAY_WEBHOOK_SECRET`, then redeploy.
Keep existing `rzp_test_...` API keys and OTP settings unchanged.

In Razorpay dashboard TEST mode add a webhook with:

```text
https://snchatbot-staging.onrender.com/payments/razorpay/webhook
```

Use the same dedicated secret, and enable `payment.captured` and `payment.failed`.
The captured event triggers the backend's verified payment finalization. Do not rely on
`order.paid` alone for fulfilment. Check event delivery logs and backend order/stock state,
including retries/duplicates and a late failure after capture.

Wake the free Render service through `/ready` before testing. Signature validation and
idempotency tests run offline too, but do not prove actual provider delivery.
Never run these checks with production credentials or real payment methods.
