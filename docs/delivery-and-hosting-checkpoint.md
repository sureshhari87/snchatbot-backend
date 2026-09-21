# Delivery and public staging checkpoint

Checked 2026-09-21. This is not approval to activate shipping or deploy production.

## Delivery source and inactive draft

The app GitHub main and migration branches contain delivery logic and test fixtures.
The backend migration branch supports `commerce.delivery_routes`; the backend default
branch does not contain that configuration. No approved live PIN coverage was identified
in those checked sources. The public GitHub repository listing did not identify a website
repository. The website itself is reachable.

Published policy: https://sona-jewels-store.sonajewellery.chatgpt.site/shipping-and-exchange

The policy dated 1 September 2026 describes complimentary standard shipping for eligible
orders within India and an estimated 7-15 business days after confirmation. Personalised
and made-to-order items can take longer. It does not define actual courier PIN coverage,
the dispatch calendar, holiday list or dispatch cutoff.

The owner approved an INACTIVE draft using 2 handling days plus 5-13 transit days,
Monday-Saturday and a 16:00 IST cutoff. This is an operational proposal, not wording taken
from the website. See `configuration/delivery-policy.draft.json`.

The draft has no approved PIN prefixes or active routes. Do not upload the draft wrapper
as `commerce.delivery_routes`: that backend setting expects an array of validated routes.
Do not activate test fixture PINs or assume every Indian PIN is carrier-serviceable.
No staging/production database configuration or shipping charges were changed.

## Public HTTPS staging

Existing HF login was verified as `sureshhari`, with `isPro=false`.
The account's listed Spaces did not include `snchatbot-staging`.
Creation of a separate public Docker Space named `sureshhari/snchatbot-staging` with
CPU Basic returned HTTP 402. No paid hardware/plan upgrade was requested, no code or secrets
were uploaded, and the production Space was not modified.

Hugging Face currently requires a paid plan to create Docker/Gradio compute Spaces,
even though CPU Basic hardware itself has no hourly charge:
https://huggingface.co/docs/hub/spaces-overview

The owner must either enable the necessary plan or choose another approved staging host.
Do not repurpose an unrelated existing Space or expose the local backend through a public
tunnel without agreeing the access/security setup. A proposed hostname is not a live URL.
Use a dedicated staging database, test payment keys and test webhook secret. A private
Space cannot receive unauthenticated provider webhook deliveries as a public backend.

## OpenAI model

Official model documentation confirms `gpt-6-astra`:
https://developers.openai.com/api/docs/models/gpt-6-astra

For the existing optional launcher prompts use:

```text
Provider HTTPS base URL: https://api.openai.com/v1
Model: gpt-6-astra
API key: enter privately in the hidden prompt
```

The account's model access, billing, and actual replies have not been verified. Do not put
the key in Git, the Android app, command arguments or chat. API usage may incur charges.
The running local staging backend reported LLM disabled and webhook unconfigured; saving
these instructions does not change that process. Use the documented opt-in launcher only
after preparing secrets. Readiness is not proof of provider authentication.
