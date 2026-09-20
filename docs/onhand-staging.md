# OnhandSMS in local staging

Stop the old staging launcher with Ctrl+C; do not stop unrelated processes.
From the backend clone run:

```powershell
& .\.venv\Scripts\python.exe scripts/local_staging.py --test-payments --onhand-sms
```

FastAPI phone/email login does not require `--firebase-auth`. That optional flag
is retained only for testing legacy Firebase token exchange.

Use the existing DIRECT Neon staging URL at the hidden prompt. Enter only
Razorpay TEST keys. Type ENABLE_SMS when asked, then privately enter the
OnhandSMS username and a ROTATED password (the previously pasted password
must not be reused). No credentials are written by this helper to disk or Git.

The final prompt asks for PHONE_AUTH_PEPPER: create one random 32+ character
secret in a password manager, label it for this Neon staging database, and reuse
the SAME value on every restart and computer. This is not an OTP, API password,
or Razorpay key. Losing/changing it changes phone identities; do not rotate it
casually or reset the database. If staging already has SMS-created users from
another setup, use that setup's existing pepper instead of creating a new one.

Configuration uses https://api.onhandsms.com/api/v2/sendsms with POST and
application/x-www-form-urlencoded. Credentials stay in the POST body, not the
URL. Payload keys: username, password, senderid, number, istamil, dlttemplateid,
message. Sender SONAJS, template 1707173372695978586; number uses local digits.
The message is exactly the supplied five-line approved template with {otp}
substituted by the backend. HTTPS/POST support was confirmed by the operator;
provider acceptance and delivery still require a controlled test.

Wait for ONHANDSMS CONFIGURED and STAGING READY. Configuration is NOT proof of
SMS delivery. Sending from the app uses REAL SMS credits even with Razorpay test
mode. Test only an owner-controlled number after approving the cost. Never share
OTPs or provider credentials in chat/logs. Test expiry, resend cooldown and failed
attempt limits; keep TESTING=0 and never return OTPs to the app as a test bypass.
No SMS is sent automatically by this launcher. No production HF secrets are changed.
This change does not configure the LLM, delivery coverage or payment webhooks.
