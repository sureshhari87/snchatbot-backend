# Android API Wiring Checklist

Use this checklist to connect the Android MVP to the live backend:

```text
https://sureshhari-snchatbot-backend.hf.space
```

Native Android calls do not need CORS. CORS matters for browser frontends and admin web panels, not Retrofit/OkHttp calls from the Android app.

## 1. App Network Setup

- Add Retrofit, OkHttp, JSON converter, coroutines, and AndroidX Security.
- Add internet permission in `AndroidManifest.xml`.
- Keep the base URL in one build config value:

```text
API_BASE_URL=https://sureshhari-snchatbot-backend.hf.space/
```

- Use release builds with HTTPS only.
- Do not hardcode admin credentials, database URLs, SMTP passwords, or API secrets in Android.

## 2. Auth Flow

Wire these endpoints first:

- `POST /register`
- `POST /login`
- `POST /refresh`
- `POST /logout`
- `POST /logout-all-devices`
- `GET /me`
- `POST /forgot-password`
- `POST /reset-password`
- `POST /verify-email`
- `POST /resend-verification`

Android behavior:

- Login uses the email address in the `username` form field.
- Store `access_token` and `refresh_token` in encrypted storage.
- Add an OkHttp interceptor that sends:

```text
Authorization: Bearer <access_token>
```

- If an API call returns `401`, call `POST /refresh`, save the new tokens, then retry once.
- If refresh fails, clear tokens and send the user to login.
- If login returns `403` with email verification message, show a verify-email reminder and resend button.

## 3. Home And Catalog

Wire:

- `GET /mobile/config`
- `GET /featured-products`
- `GET /seasonal-collections`
- `GET /categories`
- `GET /products`
- `GET /products/{product_id}`
- `GET /products/{product_id}/similar`

Use `/mobile/config` on app launch to decide whether to show OMS/order actions or LLM-dependent labels.

For LLM-backed replies, Android does not call the LLM provider directly. Always call `POST /chat`;
the backend decides whether to use rules, catalogue grounding, FAQ/policy grounding, or the optional
LLM provider. Read `answer_source`, `tool_calls`, and `guardrails` from the chat response for
debugging and support QA.

Product filters supported by `/products`:

- `q`
- `category`
- `metal`
- `min_price`
- `max_price`
- `in_stock_only`
- `gift_intent`
- `occasion`
- `limit`

## 4. Chat Screen

Wire:

- `POST /chat`
- `GET /chat/sessions`
- `GET /chat/sessions/{session_id}`
- `POST /feedback`

Chat UI should render:

- `reply` as bot text.
- `products` as horizontal product cards.
- `suggested_next_questions` as quick-reply chips.
- `applied_filters` as active filter chips.
- `handoff` as callback/appointment CTA when present.
- `response_id` for thumbs-up, thumbs-down, and not-helpful feedback.

Keep `session_id` from each response and send it back in the next chat request so follow-up messages like "show cheaper ones" or "only gold" use session memory.

## 5. Wishlist And Save For Later

Wire:

- `GET /wishlist`
- `POST /wishlist`
- `DELETE /wishlist/{item_id}`
- `GET /save-for-later`
- `POST /save-for-later`
- `DELETE /save-for-later/{item_id}`

Use product cards shared with Home, Product Detail, and Chat.

## 6. Support And Leads

Wire:

- `POST /request-callback`
- `GET /request-callbacks/my`
- `POST /appointments`
- `GET /appointments/my`
- `POST /custom-orders`
- `GET /custom-orders/my`
- `POST /complaints`
- `GET /complaints/my`

Use these from high-intent chat moments such as availability, discount, gifts, custom orders, appointments, or complaints.

## 7. Orders

Wire:

- `GET /orders/{order_reference}`
- `POST /orders/{order_reference}/cancel`
- `POST /orders/{order_reference}/return`
- `POST /orders/{order_reference}/refund`
- `POST /orders/support`
- `GET /orders/support/my`

Until real OMS is enabled, order endpoints may return `capture_only`. Show this as "Request received" rather than "Order updated".

## 8. Profile

Wire:

- `GET /me`
- `GET /users/me/addresses`
- `POST /users/me/addresses`
- `PATCH /users/me/addresses/{address_id}`
- `DELETE /users/me/addresses/{address_id}`
- `GET /users/me/notification-settings`
- `PATCH /users/me/notification-settings`

Store the push token with notification settings after Firebase Cloud Messaging is ready.

## 9. Android Smoke Test

Before Play Store release, verify:

- Fresh signup creates account.
- Unverified login shows verify-email state.
- Verified login saves tokens.
- Token refresh works after access token expiry.
- Logout removes refresh token.
- Chat returns reply, suggestions, products, and `session_id`.
- Follow-up chat keeps context.
- Wishlist add/remove works.
- Callback, appointment, and custom order forms save successfully.
- Profile address and notification settings update successfully.
- App handles offline, timeout, `401`, `403`, `422`, and `500` states gracefully.
