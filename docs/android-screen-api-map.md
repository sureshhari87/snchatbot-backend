# Android Screen API Map

This backend is ready to support the core Android MVP screens. The Android app should call `GET /mobile/config` on launch to read enabled capabilities before showing OMS or LLM-dependent features.

## Splash and Auth

Purpose: login, signup, verify-email reminder, forgot password, token refresh, logout.

Backend endpoints:

- `GET /mobile/config`
- `POST /register`
- `POST /login`
- `POST /refresh`
- `POST /logout`
- `POST /logout-all-devices`
- `GET /me`
- `POST /verify-email`
- `POST /resend-verification`
- `POST /forgot-password`
- `POST /reset-password`

Android notes:

- Store access and refresh tokens in secure storage.
- Use `POST /refresh` before forcing the user back to login.
- If login returns email-verification failure, show a verify-email reminder and resend action.

## Home

Purpose: banner, featured collections, quick chat entry, category shortcuts.

Backend endpoints:

- `GET /featured-products`
- `GET /seasonal-collections`
- `GET /categories`
- `GET /products`
- `POST /chat`

Android notes:

- Use `GET /seasonal-collections` for home banners or campaign sections.
- Use `GET /featured-products` for curated product rows.
- Quick chat entry can open the Chat screen with a prefilled message.

## Chat

Purpose: message list, quick replies, typing state, product cards, retry, escalation CTA.

Backend endpoints:

- `POST /chat`
- `GET /chat/sessions`
- `GET /chat/sessions/{session_id}`
- `POST /feedback`
- `POST /request-callback`
- `POST /appointments`

Important `POST /chat` response fields:

- `response_id`
- `reply`
- `products`
- `session_id`
- `suggestions`
- `applied_filters`
- `result_count`
- `suggested_next_questions`
- `intent`
- `confidence`
- `answer_source`
- `tool_calls`
- `guardrails`
- `lead_captured`
- `handoff`

Android notes:

- Show `products` as horizontal cards below bot replies.
- Show `suggested_next_questions` as quick replies.
- Use `handoff.channels` to display callback or appointment CTAs.
- Send thumbs-up, thumbs-down, or not-helpful feedback with `response_id`.

## Product Detail

Purpose: images, price, metal, description, stock, wishlist, ask bot about this item.

Backend endpoints:

- `GET /products/{product_id}`
- `GET /products/{product_id}/similar`
- `POST /wishlist`
- `DELETE /wishlist/{item_id}`
- `POST /save-for-later`
- `POST /chat`

Android notes:

- Use product fields `image`, `price`, `metal`, `description`, `in_stock`, and `stock_quantity`.
- For "ask bot about this item", send a message like `Tell me about product <name>` and include the active chat `session_id`.

## Wishlist

Purpose: saved products, share, remove, ask bot for similar options.

Backend endpoints:

- `GET /wishlist`
- `POST /wishlist`
- `DELETE /wishlist/{item_id}`
- `GET /products/{product_id}/similar`
- `POST /chat`

Android notes:

- Share is local Android behavior using the product name/link.
- For "similar options", call `GET /products/{product_id}/similar` or ask chat with the product category/metal.

## Orders

Purpose: order lookup, status, tracking, support actions.

Backend endpoints:

- `GET /orders/{order_reference}`
- `POST /orders/{order_reference}/cancel`
- `POST /orders/{order_reference}/return`
- `POST /orders/{order_reference}/refund`
- `POST /orders/support`
- `GET /orders/support/my`

Android notes:

- These endpoints are capture-only until `OMS_ENABLED=1` and `OMS_BASE_URL` are configured.
- Check `integration_status` in responses:
  - `synced`: OMS call succeeded.
  - `capture_only`: backend recorded request but OMS is not configured.
  - `failed`: OMS call failed and admin can review integration events.

## Support

Purpose: callback form, appointment form, support ticket, store contact.

Backend endpoints:

- `POST /request-callback`
- `GET /request-callbacks/my`
- `POST /appointments`
- `GET /appointments/my`
- `POST /complaints`
- `GET /complaints/my`
- `POST /custom-orders`
- `GET /custom-orders/my`
- `POST /orders/support`

Android notes:

- Use callback for high-intent buying questions.
- Use appointment for store visits.
- Use complaints for delivery, damage, wrong item, or service issues.
- Use custom orders for engraving, design changes, or made-to-order requests.

## Profile

Purpose: addresses, preferences, notifications, chat history, logout.

Backend endpoints:

- `GET /me`
- `GET /users/me/addresses`
- `POST /users/me/addresses`
- `PATCH /users/me/addresses/{address_id}`
- `DELETE /users/me/addresses/{address_id}`
- `GET /users/me/notification-settings`
- `PATCH /users/me/notification-settings`
- `GET /chat/sessions`
- `GET /chat/sessions/{session_id}`
- `GET /wishlist`
- `GET /save-for-later`
- `POST /logout`
- `POST /logout-all-devices`

Android notes:

- Preferences are automatically updated from chat filters, such as budget, style, category, occasion, and gift recipient.
- Notification settings are backend-stored, but push delivery still needs Android FCM wiring in the app.

## Remaining Android App Work

The backend supports these screens, but the Android app still needs:

- Secure token storage and refresh interceptor.
- Offline and retry states for network errors.
- Product card UI components shared across Home, Chat, Wishlist, and Product Detail.
- FCM push notification registration using `push_token`.
- Deep links for verify-email and reset-password flows.
- OMS production secrets before real order tracking/actions go live.
