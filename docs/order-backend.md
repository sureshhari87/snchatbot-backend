# Local Order Backend

The backend now supports a local Postgres-backed order source for the Android app. This works even before a separate external OMS is configured.

## Android Checkout Sync

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
