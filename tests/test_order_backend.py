import hashlib
import hmac
import json

from models import ExternalIntegrationEvent, OrderSnapshot


def razorpay_signature(payload: dict, secret: str) -> tuple[bytes, str]:
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return raw_body, signature


def test_local_order_sync_lookup_support_action_and_admin_update(
    client,
    auth_headers,
    admin_headers,
    db,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "OMS_ENABLED", False)
    monkeypatch.setattr(main, "OMS_BASE_URL", None)

    payload = {
        "order_reference": "ORD-LOCAL-1001",
        "status": "placed",
        "total": 24500,
        "currency": "INR",
        "customer_name": "Test Customer",
        "customer_email": "customer@example.com",
        "customer_phone": "9876543210",
        "delivery_address": {
            "line1": "12 Market Street",
            "city": "Natham",
            "postal_code": "624401",
        },
        "payment_status": "paid",
        "payment_reference": "pay_local_1001",
        "source": "android_app",
        "items": [
            {
                "product_id": "snchatbot_1",
                "backend_product_id": 1,
                "name": "Gold Ring",
                "qty": 1,
                "price": 24500,
                "image": "https://example.com/ring.jpg",
            }
        ],
    }

    sync_response = client.post("/orders/sync", headers=auth_headers, json=payload)
    assert sync_response.status_code == 200
    synced = sync_response.json()
    assert synced["order_reference"] == "ORD-LOCAL-1001"
    assert synced["items"][0]["name"] == "Gold Ring"
    assert synced["delivery_address"]["city"] == "Natham"

    my_orders = client.get("/orders/my", headers=auth_headers)
    assert my_orders.status_code == 200
    assert [order["order_reference"] for order in my_orders.json()] == ["ORD-LOCAL-1001"]

    lookup = client.get("/orders/ORD-LOCAL-1001", headers=auth_headers)
    assert lookup.status_code == 200
    assert lookup.json()["integration_status"] == "local"
    assert lookup.json()["data"]["status"] == "placed"

    support = client.post(
        "/orders/support",
        headers=auth_headers,
        json={
            "order_reference": "ORD-LOCAL-1001",
            "request_type": "delivery",
            "message": "When will this arrive?",
        },
    )
    assert support.status_code == 200
    assert support.json()["integration_status"] == "local"
    assert support.json()["status"] == "synced_to_order_backend"

    cancel = client.post(
        "/orders/ORD-LOCAL-1001/cancel",
        headers=auth_headers,
        json={"reason": "Changed my mind"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["integration_status"] == "local"
    assert cancel.json()["data"]["status"] == "cancel_requested"

    admin_orders = client.get("/admin/orders", headers=admin_headers)
    assert admin_orders.status_code == 200
    assert admin_orders.json()[0]["order_reference"] == "ORD-LOCAL-1001"

    order_id = admin_orders.json()[0]["id"]
    updated = client.patch(
        f"/admin/orders/{order_id}",
        headers=admin_headers,
        json={
            "status": "shipped",
            "tracking_number": "TRK1001",
            "tracking_url": "https://track.example.com/TRK1001",
            "expected_delivery": "2026-07-30",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "shipped"
    assert updated.json()["tracking_number"] == "TRK1001"

    order = db.query(OrderSnapshot).filter(OrderSnapshot.order_reference == "ORD-LOCAL-1001").one()
    assert order.status == "shipped"

    events = db.query(ExternalIntegrationEvent).all()
    assert {event.service for event in events} >= {"order_backend", "oms"}
    assert {event.status for event in events} >= {"synced", "local"}


def test_razorpay_webhook_updates_matching_order_snapshot(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")
    payload = {
        "order_reference": "order_test_1001",
        "status": "payment_pending",
        "total": 24500,
        "currency": "INR",
        "payment_status": "pending",
        "source": "android_app",
        "items": [
            {
                "product_id": "snchatbot_1",
                "backend_product_id": 1,
                "name": "Gold Ring",
                "qty": 1,
                "price": 24500,
            }
        ],
    }
    sync_response = client.post("/orders/sync", headers=auth_headers, json=payload)
    assert sync_response.status_code == 200

    webhook_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test_1001",
                    "order_id": "order_test_1001",
                    "status": "captured",
                    "amount": 2450000,
                    "currency": "INR",
                }
            }
        },
    }
    raw_body, signature = razorpay_signature(webhook_payload, "test_webhook_secret")

    response = client.post(
        "/payments/razorpay/webhook",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "ok"

    order = db.query(OrderSnapshot).filter_by(order_reference="order_test_1001").one()
    assert order.payment_status == "verified"
    assert order.payment_reference == "pay_test_1001"
    assert order.status == "placed"

    event = db.query(ExternalIntegrationEvent).filter_by(service="razorpay").one()
    assert event.action == "payment.captured"
    assert event.status == "updated"
    assert event.reference == "order_test_1001"


def test_razorpay_order_create_persists_checkout_order(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    import main

    captured = {}

    def fake_call_razorpay(method, path, payload):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return 200, {
            "id": "order_flutter_1001",
            "amount": payload["amount"],
            "currency": payload["currency"],
            "receipt": payload["receipt"],
            "status": "created",
        }

    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")
    monkeypatch.setattr(main, "call_razorpay", fake_call_razorpay)

    response = client.post(
        "/payments/razorpay/orders",
        headers=auth_headers,
        json={
            "amount": 2450000,
            "currency": "INR",
            "receipt": "sona-test-1001",
            "notes": {"cart_id": "cart-1001"},
            "customer_name": "Test Customer",
            "customer_email": "customer@example.com",
            "customer_phone": "9876543210",
            "items": [
                {
                    "product_id": "snchatbot_1",
                    "backend_product_id": 1,
                    "name": "Gold Ring",
                    "qty": 1,
                    "price": 24500,
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["key_id"] == "rzp_test_key"
    assert data["order_id"] == "order_flutter_1001"
    assert data["amount"] == 2450000
    assert captured["method"] == "POST"
    assert captured["path"] == "orders"
    assert captured["payload"]["notes"]["cart_id"] == "cart-1001"

    order = db.query(OrderSnapshot).filter_by(order_reference="order_flutter_1001").one()
    assert order.status == "payment_pending"
    assert order.payment_status == "pending"
    assert order.total == 24500
    assert order.source == "razorpay_checkout"

    event = (
        db.query(ExternalIntegrationEvent)
        .filter_by(service="razorpay", action="create_order")
        .one()
    )
    assert event.status == "created"
    assert event.reference == "order_flutter_1001"


def test_razorpay_payment_verify_marks_order_paid(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")

    sync_response = client.post(
        "/orders/sync",
        headers=auth_headers,
        json={
            "order_reference": "order_flutter_1002",
            "status": "payment_pending",
            "total": 12500,
            "currency": "INR",
            "payment_status": "pending",
            "source": "razorpay_checkout",
            "items": [
                {
                    "product_id": "snchatbot_2",
                    "backend_product_id": 2,
                    "name": "Gold Chain",
                    "qty": 1,
                    "price": 12500,
                }
            ],
        },
    )
    assert sync_response.status_code == 200

    payment_id = "pay_flutter_1002"
    signature = hmac.new(
        b"rzp_test_secret",
        f"order_flutter_1002|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    response = client.post(
        "/payments/razorpay/verify",
        headers=auth_headers,
        json={
            "razorpay_order_id": "order_flutter_1002",
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is True
    assert data["payment_id"] == payment_id
    assert data["order"]["status"] == "placed"
    assert data["order"]["payment_status"] == "verified"

    order = db.query(OrderSnapshot).filter_by(order_reference="order_flutter_1002").one()
    assert order.status == "placed"
    assert order.payment_status == "verified"
    assert order.payment_reference == payment_id


def test_razorpay_payment_verify_rejects_bad_signature(
    client,
    auth_headers,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")

    sync_response = client.post(
        "/orders/sync",
        headers=auth_headers,
        json={
            "order_reference": "order_flutter_1003",
            "status": "payment_pending",
            "total": 1000,
            "currency": "INR",
            "payment_status": "pending",
        },
    )
    assert sync_response.status_code == 200

    response = client.post(
        "/payments/razorpay/verify",
        headers=auth_headers,
        json={
            "razorpay_order_id": "order_flutter_1003",
            "razorpay_payment_id": "pay_flutter_1003",
            "razorpay_signature": "bad-signature",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Razorpay payment signature"


def test_razorpay_webhook_rejects_invalid_signature(client, monkeypatch):
    import main

    monkeypatch.setattr(main, "RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")
    response = client.post(
        "/payments/razorpay/webhook",
        json={"event": "payment.captured"},
        headers={"X-Razorpay-Signature": "bad-signature"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Razorpay signature"
