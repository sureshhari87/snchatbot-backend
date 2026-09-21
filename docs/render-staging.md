# Render Free staging with existing Neon

This is a separate staging backend; Hugging Face production is unchanged.
Do not create a Render database, paid disk or paid web service. Do not import the
legacy `.env.staging.example`, which is not configured for this deployment.

## Create the service

In Render use New > Blueprint. Connect GitHub repository
`sureshhari87/snchatbot-backend`, branch `codex/fastapi-commerce-migration`
(not master/main), and Blueprint path `render.yaml`.

Verify the preview creates ONE new web service, `snchatbot-staging`, on the FREE
plan with no databases or disks. Do not attach an existing production service.
Automatic deployments are off; deploy reviewed commits manually.

Enter these only in Render's private settings:

| Setting | Value |
| --- | --- |
| `DATABASE_URL` | Neon project `snchatbot-production`, branch **staging**, database `snchatbot_staging`, role `staging_owner`: DIRECT PostgreSQL URL, pooling OFF, TLS enabled. |
| `STAGING_NEON_HOST` | Only the hostname from that same URL, `ep-...neon.tech`, without protocol, credentials, port or path. |

Render generates `SECRET_KEY`. Preserve it across restarts. The launcher checks the
role/database, independently entered hostname, existing schema and nonempty catalogue.
Still confirm the BRANCH in Neon; database/role names cannot prove branch identity.
Direct connections are intentional for this single-worker staging guard.
No migration/reset/import runs. Existing backend initialization still runs after preflight.

If using New > Web Service instead of Blueprint, select Docker, Free, the same
repo/branch, Dockerfile `./Dockerfile`, health path `/ready`, and Docker Command:

```text
python scripts/render_staging.py
```

Add the two settings above plus `APP_ENV=staging`, `PORT=10000`, and a unique random
32+ character `SECRET_KEY` through the private dashboard. Keep `SMS_OTP_ENABLED=0`
and `LLM_ENABLED=0` initially. Do not use the Dockerfile's default command for staging.

If Render requests a card or paid upgrade, stop. A stored payment method can allow
usage-overage charges; without one, applicable limits suspend services/builds.

## Verify readiness

Use the ACTUAL HTTPS URL Render assigns, not an example hostname.
Open `/ready`: require HTTP 200, `status=ok` and database status `ok`.
Then check `/products?limit=50&in_stock_only=false` for the existing catalogue.
Share only the public URL and redacted error text, never credentials or database URLs.
If the guard refuses startup, check the staging URL, hostname and schema privately.
Do not bypass the guard or migrate production to clear a staging error.

## Enable providers after readiness

Enter these in Render Environment, never GitHub or Flutter:

- OTP: `SMS_OTP_ENABLED=1`, `ONHANDSMS_USERNAME`, rotated `ONHANDSMS_PASSWORD`,
  and the SAME `PHONE_AUTH_PEPPER` used for this staging database. A new pepper
  per deploy changes phone identity. The launcher supplies the tested HTTPS POST/form
  mapping, sender and DLT template. Real SMS consumes credits.
- Payments: `RAZORPAY_KEY_ID=rzp_test_...`, `RAZORPAY_KEY_SECRET`, and a dedicated
  32+ character `RAZORPAY_WEBHOOK_SECRET`. Live key IDs are rejected.
- AI: follow `docs/staging-payment-ai-verification.md` after preparing the approved
  provider/model/key and usage budget. Free hosting does not make API calls free.

Redeploy after editing secrets and check `/ready`. Configuration loaded does not prove
actual provider access. Keep the provider enable flags aligned in `render.yaml` before
later Blueprint syncs, which can reapply their initial disabled values.
Do not add Firebase service-account credentials or Hugging Face production secrets.

In Razorpay TEST mode use the actual staging HTTPS URL plus
`/payments/razorpay/webhook`, with the same dedicated test webhook secret. Verify
signature rejection, duplicate captures, late failures and resulting order/stock state.

## Connect the phone

Replace the example with the real deployed URL:

```powershell
flutter run -d ZA222PH329 --disable-dds --dart-define=SNCHATBOT_API_BASE_URL=https://YOUR-ACTUAL-STAGING-HOST.onrender.com
```

No ADB reverse is needed for HTTPS. Preserve app data; stop on a signing mismatch.
Sign out and back in if the old local JWT is rejected. Use staging users and test payments.

Free Render sleeps after 15 idle minutes; wait for `/ready` before OTP/checkout/webhook
tests. Local files are ephemeral: use Neon and Cloudinary, not SQLite. Free Render blocks
SMTP ports, so email verification requires a separately configured HTTPS email provider.
Phone OTP is separate. Free hosting does not guarantee production availability.

## References

- https://render.com/docs/blueprint-spec
- https://render.com/docs/free
- https://render.com/docs/configure-environment-variables
- https://render.com/docs/health-checks
