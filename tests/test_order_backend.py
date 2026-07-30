from models import ExternalIntegrationEvent, OrderSnapshot


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
