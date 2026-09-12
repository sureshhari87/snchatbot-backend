import hashlib
import hmac
import json

from models import ExternalIntegrationEvent, OrderSnapshot, Product


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
        "metadata": {
            "delivery_promises": [
                {
                    "product_id": "snchatbot_1",
                    "earliest_date": "2026-07-28",
                    "latest_date": "2026-07-30",
                }
            ]
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
    assert synced["metadata"]["delivery_promises"][0]["product_id"] == "snchatbot_1"
    assert synced["delivery_promises"][0]["latest_date"] == "2026-07-30"

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


def test_order_sync_accepts_flutter_formatted_delivery_address(client, auth_headers):
    response = client.post(
        "/orders/sync",
        headers=auth_headers,
        json={
            "order_reference": "ORD-FORMATTED-1001",
            "status": "placed",
            "total": 15000,
            "currency": "INR",
            "delivery_address": "12 Market Street, Natham, Tamil Nadu, 624401",
            "payment_status": "paid",
        },
    )

    assert response.status_code == 200
    assert response.json()["delivery_address"] == {
        "formatted": "12 Market Street, Natham, Tamil Nadu, 624401"
    }


def test_razorpay_webhook_updates_matching_order_snapshot(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")

    def fake_call_razorpay(method, path, payload=None):
        assert method == "GET"
        assert path == "payments/pay_test_1001"
        return 200, {
            "id": "pay_test_1001",
            "order_id": "order_test_1001",
            "status": "captured",
            "amount": 2450000,
            "currency": "INR",
        }

    monkeypatch.setattr(main, "call_razorpay", fake_call_razorpay)
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

    event = (
        db.query(ExternalIntegrationEvent)
        .filter_by(service="razorpay", action="payment.captured")
        .one()
    )
    assert event.action == "payment.captured"
    assert event.status == "finalized"
    assert event.reference == "order_test_1001"

    product = db.query(Product).filter(Product.id == 1).one()
    assert product.stock_quantity == 7


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
            "amount": 1899900,
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
                    "qty": 1,
                    "cart_item_id": "cart-line-1",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["key_id"] == "rzp_test_key"
    assert data["keyId"] == "rzp_test_key"
    assert data["order_id"] == "order_flutter_1001"
    assert data["razorpayOrderId"] == "order_flutter_1001"
    assert data["amount"] == 1899900
    assert data["payableTotal"] == 18999
    assert data["server_calculated"] is True
    assert captured["method"] == "POST"
    assert captured["path"] == "orders"
    assert captured["payload"]["notes"]["cart_id"] == "cart-1001"
    assert captured["payload"]["amount"] == 1899900

    order = db.query(OrderSnapshot).filter_by(order_reference="order_flutter_1001").one()
    assert order.status == "payment_pending"
    assert order.payment_status == "pending"
    assert order.total == 18999
    assert order.source == "razorpay_checkout"
    raw_payload = json.loads(order.raw_payload)
    assert raw_payload["metadata"]["server_authoritative_pricing"] is True
    assert raw_payload["metadata"]["cart_cleanup"]["cart_item_ids"] == ["cart-line-1"]

    event = (
        db.query(ExternalIntegrationEvent)
        .filter_by(service="razorpay", action="create_order")
        .one()
    )
    assert event.status == "created"
    assert event.reference == "order_flutter_1001"


def test_razorpay_order_create_rejects_client_amount_mismatch(
    client,
    auth_headers,
    monkeypatch,
):
    import main

    def fail_if_called(method, path, payload=None):
        raise AssertionError("Razorpay should not be called for a tampered amount")

    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")
    monkeypatch.setattr(main, "call_razorpay", fail_if_called)

    response = client.post(
        "/payments/razorpay/orders",
        headers=auth_headers,
        json={
            "amount": 100,
            "currency": "INR",
            "items": [{"backend_product_id": 1, "qty": 1}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Checkout amount changed. Refresh your cart and try again."


def test_razorpay_order_create_uses_firestore_authority_when_enabled(
    client,
    auth_headers,
    verified_user,
    db,
    monkeypatch,
):
    import main

    captured = {}

    def fake_verify_firebase_id_token(_token):
        return {
            "aud": "sona-test-firebase-project",
            "iss": "https://securetoken.google.com/sona-test-firebase-project",
            "sub": "firebaseCheckoutUid",
            "email": verified_user.email,
            "email_verified": True,
        }

    def fake_firestore_calculation(db_session, payload, user):
        assert db_session is db
        assert user.firebase_uid == "firebaseCheckoutUid"
        assert payload.items[0].product_id == "firestore-ring-1"
        return {
            "amount": 2490000,
            "currency": "INR",
            "receipt": "sona-firestore-1001",
            "items": [
                main.OrderItemSync(
                    product_id="firestore-ring-1",
                    backend_product_id=None,
                    name="Firestore Ring",
                    qty=1,
                    price=24900,
                    image="https://example.com/firestore-ring.jpg",
                )
            ],
            "metadata": {
                "authority": "firestore",
                "server_authoritative_pricing": True,
                "firebase_uid": "firebaseCheckoutUid",
                "subtotal": 25000,
                "payable_total": 24900,
                "coupon": {"code": "SONA100", "discount": 100, "status": "applied"},
                "rewards": {"consumed_points": 0, "status": "not_requested"},
                "cart_cleanup": {
                    "status": "pending_firestore_transaction",
                    "cart_item_ids": ["cart-firestore-ring-1"],
                },
                "items": [
                    {
                        "product_id": "firestore-ring-1",
                        "firestore_product_id": "firestore-ring-1",
                        "cart_item_id": "cart-firestore-ring-1",
                        "name": "Firestore Ring",
                        "qty": 1,
                        "price": 24900,
                    }
                ],
            },
            "payable_total": 24900,
            "coupon_discount": 100,
            "reward_points_used": 0,
            "authority": "firestore",
        }

    def fake_call_razorpay(method, path, payload):
        captured["razorpay_payload"] = payload
        return 200, {
            "id": "order_firestore_1001",
            "amount": payload["amount"],
            "currency": payload["currency"],
            "receipt": payload["receipt"],
            "status": "created",
        }

    def fake_create_firestore_payment_attempt(user, order, calculation, payload, response):
        captured["attempt"] = {
            "user_id": user.id,
            "firebase_uid": user.firebase_uid,
            "order_reference": order.order_reference,
            "amount": calculation["amount"],
            "razorpay_order_id": response["id"],
        }

    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")
    monkeypatch.setattr(main, "verify_firebase_id_token", fake_verify_firebase_id_token)
    monkeypatch.setattr(main, "firestore_checkout_calculation", fake_firestore_calculation)
    monkeypatch.setattr(main, "call_razorpay", fake_call_razorpay)
    monkeypatch.setattr(
        main,
        "create_firestore_payment_attempt",
        fake_create_firestore_payment_attempt,
    )

    response = client.post(
        "/payments/razorpay/orders",
        headers=auth_headers,
        json={
            "currency": "INR",
            "firebase_id_token": "firebase-id-token-for-checkout",
            "coupon_code": "SONA100",
            "items": [
                {
                    "product_id": "firestore-ring-1",
                    "qty": 1,
                    "cart_item_id": "cart-firestore-ring-1",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == 2490000
    assert body["payableTotal"] == 24900
    assert body["couponDiscount"] == 100
    assert body["server_calculated"] is True
    assert captured["razorpay_payload"]["amount"] == 2490000
    assert captured["razorpay_payload"]["notes"]["server_calculated"] == "true"
    assert captured["attempt"]["firebase_uid"] == "firebaseCheckoutUid"

    order = db.query(OrderSnapshot).filter_by(order_reference="order_firestore_1001").one()
    raw_payload = json.loads(order.raw_payload)
    assert raw_payload["metadata"]["authority"] == "firestore"
    assert raw_payload["metadata"]["firebase_uid"] == "firebaseCheckoutUid"


def test_razorpay_payment_verify_marks_order_paid(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")

    def fake_call_razorpay(method, path, payload=None):
        assert method == "GET"
        assert path == "payments/pay_flutter_1002"
        return 200, {
            "id": "pay_flutter_1002",
            "order_id": "order_flutter_1002",
            "status": "captured",
            "amount": 1250000,
            "currency": "INR",
        }

    monkeypatch.setattr(main, "call_razorpay", fake_call_razorpay)

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
    assert data["paymentId"] == payment_id
    assert data["order_reference"] == "order_flutter_1002"
    assert data["orderId"] == "order_flutter_1002"
    assert data["status"] == "placed"
    assert data["payment_status"] == "verified"
    assert data["paymentStatus"] == "verified"
    assert data["order"]["status"] == "placed"
    assert data["order"]["payment_status"] == "verified"

    order = db.query(OrderSnapshot).filter_by(order_reference="order_flutter_1002").one()
    assert order.status == "placed"
    assert order.payment_status == "verified"
    assert order.payment_reference == payment_id

    product = db.query(Product).filter(Product.id == 2).one()
    assert product.stock_quantity == 4

    second_response = client.post(
        "/payments/razorpay/verify",
        headers=auth_headers,
        json={
            "razorpay_order_id": "order_flutter_1002",
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        },
    )
    assert second_response.status_code == 200
    assert second_response.json()["verified"] is True
    db.refresh(product)
    assert product.stock_quantity == 4


def test_razorpay_payment_verify_uses_firestore_finalizer_for_firestore_orders(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")

    def fake_call_razorpay(method, path, payload=None):
        assert path == "payments/pay_firestore_1002"
        return 200, {
            "id": "pay_firestore_1002",
            "order_id": "order_firestore_1002",
            "status": "captured",
            "amount": 2490000,
            "currency": "INR",
        }

    def fake_firestore_finalizer(order, payment_id, payment, source, event_id=None):
        assert order.order_reference == "order_firestore_1002"
        assert payment_id == "pay_firestore_1002"
        assert payment["status"] == "captured"
        assert source == "verify"
        return {
            "status": "finalized",
            "inventory_updates": [
                {
                    "product_id": "firestore-ring-1",
                    "name": "Firestore Ring",
                    "qty": 1,
                    "before": 4,
                    "after": 3,
                }
            ],
            "reward_points_consumed": 25,
            "purchase_rewards_awarded": 249,
            "referral_rewards_awarded": 0,
            "coupon_redeemed": True,
            "gift_voucher_redeemed": False,
            "cart_cleanup": {"status": "completed", "removed_count": 1},
        }

    monkeypatch.setattr(main, "call_razorpay", fake_call_razorpay)
    monkeypatch.setattr(main, "finalize_firestore_commerce_payment", fake_firestore_finalizer)

    sync_response = client.post(
        "/orders/sync",
        headers=auth_headers,
        json={
            "order_reference": "order_firestore_1002",
            "status": "payment_pending",
            "total": 24900,
            "currency": "INR",
            "payment_status": "pending",
            "source": "razorpay_checkout",
            "items": [
                {
                    "product_id": "firestore-ring-1",
                    "name": "Firestore Ring",
                    "qty": 1,
                    "price": 24900,
                }
            ],
            "raw_payload": {
                "metadata": {
                    "authority": "firestore",
                    "firebase_uid": "firebaseCheckoutUid",
                    "coupon": {"code": "SONA100", "discount": 100},
                    "rewards": {"consumed_points": 25},
                    "cart_cleanup": {"cart_item_ids": ["cart-firestore-ring-1"]},
                    "items": [
                        {
                            "product_id": "firestore-ring-1",
                            "firestore_product_id": "firestore-ring-1",
                            "cart_item_id": "cart-firestore-ring-1",
                            "name": "Firestore Ring",
                            "qty": 1,
                            "price": 24900,
                        }
                    ],
                }
            },
        },
    )
    assert sync_response.status_code == 200

    signature = hmac.new(
        b"rzp_test_secret",
        b"order_firestore_1002|pay_firestore_1002",
        hashlib.sha256,
    ).hexdigest()

    response = client.post(
        "/payments/razorpay/verify",
        headers=auth_headers,
        json={
            "razorpay_order_id": "order_firestore_1002",
            "razorpay_payment_id": "pay_firestore_1002",
            "razorpay_signature": signature,
        },
    )

    assert response.status_code == 200
    assert response.json()["verified"] is True
    order = db.query(OrderSnapshot).filter_by(order_reference="order_firestore_1002").one()
    raw_payload = json.loads(order.raw_payload)
    finalization = raw_payload["razorpay_finalization"]
    assert finalization["reward_points_consumed"] == 25
    assert finalization["purchase_rewards_awarded"] == 249
    assert finalization["coupon_redeemed"] is True
    assert finalization["cart_cleanup"]["status"] == "completed"


def test_razorpay_payment_verify_rejects_amount_mismatch(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")

    def fake_call_razorpay(method, path, payload=None):
        return 200, {
            "id": "pay_flutter_1004",
            "order_id": "order_flutter_1004",
            "status": "captured",
            "amount": 100,
            "currency": "INR",
        }

    monkeypatch.setattr(main, "call_razorpay", fake_call_razorpay)

    sync_response = client.post(
        "/orders/sync",
        headers=auth_headers,
        json={
            "order_reference": "order_flutter_1004",
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

    signature = hmac.new(
        b"rzp_test_secret",
        b"order_flutter_1004|pay_flutter_1004",
        hashlib.sha256,
    ).hexdigest()

    response = client.post(
        "/payments/razorpay/verify",
        headers=auth_headers,
        json={
            "razorpay_order_id": "order_flutter_1004",
            "razorpay_payment_id": "pay_flutter_1004",
            "razorpay_signature": signature,
        },
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"] == "Razorpay payment amount or currency did not match the order"
    )
    product = db.query(Product).filter(Product.id == 2).one()
    assert product.stock_quantity == 5


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


def test_razorpay_webhook_duplicate_capture_is_idempotent(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")

    def fake_call_razorpay(method, path, payload=None):
        return 200, {
            "id": "pay_dup_1001",
            "order_id": "order_dup_1001",
            "status": "captured",
            "amount": 1899900,
            "currency": "INR",
        }

    monkeypatch.setattr(main, "call_razorpay", fake_call_razorpay)

    sync_response = client.post(
        "/orders/sync",
        headers=auth_headers,
        json={
            "order_reference": "order_dup_1001",
            "status": "payment_pending",
            "total": 18999,
            "currency": "INR",
            "payment_status": "pending",
            "source": "razorpay_checkout",
            "items": [
                {
                    "product_id": "snchatbot_1",
                    "backend_product_id": 1,
                    "name": "Classic Gold Ring",
                    "qty": 1,
                    "price": 18999,
                }
            ],
        },
    )
    assert sync_response.status_code == 200

    webhook_payload = {
        "id": "evt_duplicate_capture",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_dup_1001",
                    "order_id": "order_dup_1001",
                    "status": "captured",
                    "amount": 1899900,
                    "currency": "INR",
                }
            }
        },
    }
    raw_body, signature = razorpay_signature(webhook_payload, "test_webhook_secret")

    first = client.post(
        "/payments/razorpay/webhook",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
        },
    )
    second = client.post(
        "/payments/razorpay/webhook",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    product = db.query(Product).filter(Product.id == 1).one()
    assert product.stock_quantity == 7
    finalized_events = (
        db.query(ExternalIntegrationEvent)
        .filter_by(service="razorpay", action="finalize_payment", status="finalized")
        .all()
    )
    assert len(finalized_events) == 1
    webhook_events = (
        db.query(ExternalIntegrationEvent)
        .filter_by(service="razorpay", action="webhook_event", reference="evt_duplicate_capture")
        .all()
    )
    assert len(webhook_events) == 1


def test_razorpay_webhook_failed_event_does_not_downgrade_verified_order(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    import main

    monkeypatch.setattr(main, "RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "rzp_test_secret")

    sync_response = client.post(
        "/orders/sync",
        headers=auth_headers,
        json={
            "order_reference": "order_failed_late",
            "status": "placed",
            "total": 18999,
            "currency": "INR",
            "payment_status": "verified",
            "payment_reference": "pay_failed_late",
            "source": "razorpay_checkout",
            "items": [
                {
                    "product_id": "snchatbot_1",
                    "backend_product_id": 1,
                    "name": "Classic Gold Ring",
                    "qty": 1,
                    "price": 18999,
                }
            ],
        },
    )
    assert sync_response.status_code == 200

    webhook_payload = {
        "id": "evt_failed_late",
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_failed_late",
                    "order_id": "order_failed_late",
                    "status": "failed",
                    "amount": 1899900,
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
    order = db.query(OrderSnapshot).filter_by(order_reference="order_failed_late").one()
    assert order.payment_status == "verified"
    event = (
        db.query(ExternalIntegrationEvent)
        .filter_by(service="razorpay", action="payment.failed")
        .one()
    )
    assert event.status == "ignored_out_of_order"


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
