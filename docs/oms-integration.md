# OMS And Order Integration

The backend supports order lookup and order support actions through an external Order Management System (OMS). Until an OMS is configured, customer requests are saved locally and returned as `capture_only`.

## Hugging Face Secrets

Set these only after your OMS API is ready:

```text
OMS_ENABLED=1
OMS_BASE_URL=https://your-oms.example.com/api
OMS_API_KEY=replace-with-oms-bearer-token
OMS_TIMEOUT_SECONDS=10
```

Keep `OMS_API_KEY` as a Hugging Face Secret, not an Android value.

## Required OMS Contract

The backend currently calls these external OMS endpoints:

```text
GET  {OMS_BASE_URL}/orders/{order_reference}
POST {OMS_BASE_URL}/orders/{order_reference}/cancel
POST {OMS_BASE_URL}/orders/{order_reference}/return
POST {OMS_BASE_URL}/orders/{order_reference}/refund
```

The backend sends this header when `OMS_API_KEY` is configured:

```text
Authorization: Bearer <OMS_API_KEY>
```

Action request payload:

```json
{
  "reason": "Customer requested cancellation",
  "message": "Please cancel before shipping"
}
```

Recommended OMS lookup response:

```json
{
  "order_reference": "ORD-1001",
  "status": "packed",
  "payment_status": "paid",
  "delivery_status": "preparing",
  "tracking_url": "https://tracking.example/ORD-1001",
  "items": [
    {
      "sku": "RING-GOLD-101",
      "name": "Classic Gold Ring",
      "quantity": 1,
      "price": 18999
    }
  ]
}
```

Recommended OMS action response:

```json
{
  "order_reference": "ORD-1001",
  "status": "cancel_requested",
  "message": "Cancellation request received"
}
```

## Backend Response Statuses

Android should handle these `integration_status` values:

- `synced`: OMS call succeeded.
- `capture_only`: backend saved the request, but OMS is not configured or order reference is missing.
- `disabled`: lookup requested while OMS is not configured.
- `failed`: OMS call failed; backend saved an audit event for admin review.

## Backend Customer Endpoints

```text
GET  /orders/{order_reference}
POST /orders/{order_reference}/cancel
POST /orders/{order_reference}/return
POST /orders/{order_reference}/refund
POST /orders/support
GET  /orders/support/my
```

All customer order endpoints require a logged-in user token.

## Admin Review Endpoints

```text
GET   /admin/integrations/status
GET   /admin/integrations/events?service=oms
GET   /admin/orders/support
PATCH /admin/orders/support/{request_id}
```

Use these to monitor OMS failures, capture-only requests, and customer escalation state.

## Production Checklist

- Configure `OMS_BASE_URL` and `OMS_API_KEY` in Hugging Face.
- Run `GET /mobile/config` and confirm `oms_connected` is `true`.
- Run one real order lookup with a test order.
- Run one cancel/return/refund request against a test order.
- Confirm `/admin/integrations/events?service=oms` records each call.
- Confirm Android shows `capture_only`, `synced`, and `failed` states clearly.

