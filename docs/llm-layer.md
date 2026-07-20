# LLM Layer

The chatbot works without an LLM. When LLM variables are present, the backend adds a guarded
OpenAI-compatible chat-completions layer on top of the existing catalogue/rules engine.

## Runtime Flow

1. Parse customer message into filters and intent.
2. Search local catalogue first.
3. Select relevant FAQ/policy knowledge-base rows.
4. Send only grounded context to the LLM provider.
5. Validate the generated reply.
6. Fall back to the deterministic rules reply if the provider fails or returns unsafe text.

The LLM is skipped for order status, return/refund, complaint, and human-handoff flows.

## Hugging Face Secrets

Add these only when you are ready to use a real provider:

```env
LLM_ENABLED=1
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=replace-with-provider-api-key
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT_SECONDS=20
LLM_MAX_TOKENS=350
```

`LLM_BASE_URL` can be either the provider base URL or the full `/chat/completions` URL. The
backend normalizes both forms.

For MVP production, keep these off until you have budget limits and provider billing alerts:

```env
LLM_ENABLED=0
LLM_BASE_URL=
LLM_API_KEY=
```

## Guardrails

The backend instructs the model to answer only from:

- filtered product results
- product stock/price fields
- FAQ and policy rows
- the rules fallback reply

The backend rejects unsafe generated text containing:

- investment/profit guarantees
- medical claims
- password/OTP requests
- card, bank, or UPI collection language
- URLs/payment links
- overly long replies

Rejected or failed LLM replies return the normal rules-based answer with:

```json
{
  "answer_source": "rules_fallback"
}
```

Successful LLM replies return:

```json
{
  "answer_source": "llm_grounded_catalog",
  "tool_calls": ["knowledge_lookup", "llm_completion"]
}
```

`knowledge_lookup` appears only when a relevant FAQ or policy row was used.

## Android Handling

On app launch, call:

```http
GET /mobile/config
```

Use:

```json
{
  "capabilities": {
    "llm_grounded_answers": true
  }
}
```

Do not hard-code LLM availability in Android. Render the same chat UI either way, but optionally
show a small "AI assisted" internal/debug label only when `answer_source` starts with `llm_`.

## Admin Checks

Use an admin token:

```http
GET /admin/integrations/status
```

Expected when enabled:

```json
{
  "llm": {
    "enabled": true,
    "base_url_configured": true,
    "api_key_configured": true
  }
}
```

The backend stores provider audit rows in `external_integration_events` with `service="llm"`.

## Verification

Run locally:

```cmd
python -m pytest tests\test_production_integrations.py -q
python -m pytest -q
```

Run live smoke tests after deployment:

```cmd
set LIVE_API_BASE_URL=https://sureshhari-snchatbot-backend.hf.space
set LIVE_API_EXPECT_LATEST_CHAT_CONTRACT=1
python -m pytest tests\test_live_api.py -q
```

