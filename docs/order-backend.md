# Local Order Backend

The backend now supports a local Postgres-backed order source for the Android app. This works even before a separate external OMS is configured.

## Android Razorpay Checkout

For production payments, Android should create Razorpay orders through FastAPI instead of creating them in Firebase Functions or in the app.

```http
POST /payments/razorpay/orders
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "currency": "INR",
  "receipt": "sona-cart-1001",
  "notes": {
    "cart_id": "cart-1001"
  },
  "coupon_code": "SONA100",
  "reward_points_requested": 0,
  "firebase_id_token": "optional-fresh-firebase-id-token",
  "customer_name": "Customer Name",
  "customer_email": "customer@example.com",
  "customer_phone": "9876543210",
  "delivery_address": {
    "line1": "12 Market Street",
    "city": "Natham",
    "postal_code": "624401"
  },
  "items": [
    {
      "product_id": "snchatbot_1",
      "backend_product_id": 1,
      "qty": 1,
      "cart_item_id": "flutter-cart-doc-id"
    }
  ]
}
```

When `FIRESTORE_COMMERCE_ENABLED=1` and Firebase Admin credentials are configured, the backend calculates
`amount` from Firestore `products`, `gold_rates/today`, coupons, gift vouchers, reward points, and current
cart ownership. Otherwise it falls back to the local Postgres product table for Sona AI/admin catalogue items.

Flutter may send `amount` during migration only as a sanity check; if the client amount does not match the
backend amount, the request is rejected before Razorpay is called. Do not send trusted prices, coupon
discounts, or reward deductions from Flutter.

Response:

```json
{
  "key_id": "rzp_live_xxx",
  "order_id": "order_xxx",
  "order_reference": "order_xxx",
  "local_order_id": 123,
  "amount": 2450000,
  "currency": "INR",
  "receipt": "sona-cart-1001",
  "status": "created"
}
```

For Flutter compatibility, the response also includes camelCase aliases such as `keyId`, `razorpayOrderId`,
`payableTotal`, `couponDiscount`, and `rewardPointsUsed`. Always open Razorpay Checkout with the amount
returned by the backend.

Use `key_id`, `order_id`, `amount`, and `currency` to open Razorpay Checkout. After Checkout success, verify on the backend:

```http
POST /payments/razorpay/verify
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "razorpay_order_id": "order_xxx",
  "razorpay_payment_id": "pay_xxx",
  "razorpay_signature": "signature_from_checkout"
}
```

A valid Checkout signature is only the first check. The backend then confirms the payment status with Razorpay,
compares the captured amount and currency with the local order, and runs the shared finalizer once. For
Firestore-authoritative orders, that finalizer updates Firestore products, reward points, coupon/gift-voucher
redemptions, `orders/{orderId}`, `payment_attempts/{orderId}`, and cart cleanup in one Firestore transaction.
For local catalogue orders, it decrements local Postgres inventory once. The local order snapshot is updated to
`status=placed` and `payment_status=verified`.
The verify response includes both a nested `order` object and top-level fields such as `order_reference`, `status`, `payment_status`, `total`, `currency`, and `items` for payment recovery screens.

The same finalization operation is used by `POST /payments/razorpay/verify` and the Razorpay webhook, so retries
and duplicate events do not decrement stock twice.

## Firestore Commerce Authority

Use this mode for the production Flutter app because your live cart, products, coupons, rewards, and order
screens are Firestore-backed.

Required Hugging Face secrets:

```env
FIREBASE_PROJECT_ID=your-firebase-project-id
FIREBASE_AUTH_ENABLED=1
FIRESTORE_COMMERCE_ENABLED=1
FIREBASE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
```

Optional tuning:

```env
FIRESTORE_PRODUCTS_COLLECTION=products
FIRESTORE_USERS_COLLECTION=users
FIRESTORE_ORDERS_COLLECTION=orders
FIRESTORE_PAYMENT_ATTEMPTS_COLLECTION=payment_attempts
FIRESTORE_COUPONS_COLLECTION=coupons
FIRESTORE_GIFT_VOUCHERS_COLLECTION=gift_vouchers
FIRESTORE_GOLD_RATES_COLLECTION=gold_rates
FIRESTORE_GOLD_RATES_DOCUMENT=today
REWARD_POINT_VALUE_RUPEES=1
PURCHASE_REWARD_RUPEES_PER_POINT=100
REFERRAL_REWARD_POINTS=0
```

For existing users, the app should call `POST /auth/firebase` again, or send `firebase_id_token` during
`POST /payments/razorpay/orders`, so FastAPI can stamp the local user with the trusted Firebase UID.

## Razorpay Webhook And Reconciliation

Configure Razorpay to call:

```text
https://sureshhari-snchatbot-backend.hf.space/payments/razorpay/webhook
```

The webhook verifies the raw-body signature, records the event id, ignores duplicates, handles out-of-order
failed events without downgrading verified orders, and records refund/dispute events for admin follow-up.

If Razorpay captured a payment but the webhook was missed, an admin can run:

```http
POST /admin/payments/razorpay/reconcile?limit=50
Authorization: Bearer <admin_access_token>
```

This checks pending local Razorpay orders against Razorpay and finalizes any captured payment using the same
idempotent finalizer.

## Legacy Checkout Sync

After Firebase/Razorpay checkout finalizes an order, sync a copy to FastAPI:

```http
POST /orders/sync
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "order_reference": "ORD-1001",
  "status": "placed",
  "total": 24500,
  "currency": "INR",
  "customer_name": "Customer Name",
  "customer_email": "customer@example.com",
  "customer_phone": "9876543210",
  "delivery_address": {
    "line1": "12 Market Street",
    "city": "Natham",
    "postal_code": "624401"
  },
  "payment_status": "paid",
  "payment_reference": "razorpay_payment_id",
  "source": "android_app",
  "items": [
    {
      "product_id": "snchatbot_1",
      "backend_product_id": 1,
      "name": "Gold Ring",
      "qty": 1,
      "price": 24500,
      "image": "https://example.com/ring.jpg"
    }
  ]
}
```

## Customer APIs

- `GET /orders/my` lists the signed-in user's synced orders.
- `GET /orders/{order_reference}` returns external OMS data when configured, otherwise local order data.
- `POST /orders/{order_reference}/cancel` records a cancellation request locally or sends it to OMS.
- `POST /orders/{order_reference}/return` records a return request locally or sends it to OMS.
- `POST /orders/{order_reference}/refund` records a refund request locally or sends it to OMS.
- `POST /orders/support` attaches status, delivery, cancellation, return, refund, or support requests to the order.

## Integration Status Values

- `synced`: external OMS request succeeded.
- `local`: local Postgres order backend handled the lookup/action.
- `local_fallback`: external OMS failed, but local order data was available.
- `capture_only`: request was captured, but no external OMS or local order matched.
- `failed`: external OMS request failed and no local fallback handled it.

## Admin APIs

- `GET /admin/orders` lists local order snapshots.
- `PATCH /admin/orders/{order_id}` updates status, payment status, tracking number, tracking URL, expected delivery, or delivery address.
- `GET /admin/orders/support` lists order support requests.

## External OMS Later

When you adopt a real OMS provider, set these Hugging Face secrets:

```env
OMS_ENABLED=1
OMS_BASE_URL=https://your-oms.example.com/api
OMS_API_KEY=replace-with-secret-token
OMS_TIMEOUT_SECONDS=10
```

Android should continue using the same FastAPI endpoints. The backend decides whether to use the external OMS or the local fallback.
