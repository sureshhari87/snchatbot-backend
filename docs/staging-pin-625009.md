# Owner-approved staging test PIN: 625009

The owner approved this exact PIN for staging after the inactive timing draft.
This is NOT courier verification or production serviceability approval.

- Standard delivery only; PIN `625009` (not prefix `625`).
- 2 handling + 5-13 transit business days; Monday-Saturday; 16:00 IST cutoff.
- Holiday list is empty for this staging fixture, not an approved production calendar.
- Test with ordinary in-stock products, not customised/made-to-order products.

## If setup stopped with OperationalError

A successful TCP port check does not verify database authentication, TLS or SQL permissions.
Do not reset passwords, weaken TLS, or retry writes merely because port 5432 is reachable.
Use this read-only diagnostic on the current computer:

```powershell
cd C:\Users\sures\sona_jewellery_app\.codex\fastapi-commerce
& "C:\Users\sures\snchatbot-staging\.venv\Scripts\python.exe" scripts/check_staging_delivery.py
```

Enter the expected staging hostname and DIRECT staging URL in its prompts, not command
arguments. It uses a read-only transaction, checks identity/schema/current delivery state,
and reports only a fixed failure category and phase. No raw PostgreSQL errors or URL are
printed. The connection timeout is 30 seconds for diagnosis only; write behavior is unchanged.

- `AUTHENTICATION`: privately recopy the selected staging role's connection URL; do not
  rotate a password used by Render without planning the corresponding secret update.
- `TLS`: keep TLS enabled; investigate local client/certificate/channel-binding support.
- `CONNECTION_TIMEOUT`, `DNS`, `CONNECTION_REFUSED`, `DISCONNECTED`: investigate the local
  connection path and endpoint availability; a healthy Render connection is a different path.
- `PERMISSION`: review staging role access, not production grants.
- `UNCLASSIFIED`: the safe classifier cannot determine the cause; do not guess from the label.
- `delivery=already_configured`: the approved route is present, so no write retry is needed.
- `delivery=not_configured`: configuration is still pending; review any permission flags
  before using the write helper below.
- `delivery=needs_review`: existing data differs; it is not printed or overwritten.

Share only the diagnostic JSON result. A successful read check does not prove that every
subsequent write/commit will succeed. This diagnostic performs no schema/data changes.

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
