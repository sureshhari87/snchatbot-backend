import main
from models import OrderSnapshot, OrderSnapshotItem, Product
from schemas import OrderSyncRequest


def test_customer_cannot_fabricate_paid_order(client, auth_headers, db):
    response = client.post(
        "/orders/sync",
        headers=auth_headers,
        json={
            "order_reference": "order_forged_test",
            "source": "razorpay_checkout",
            "payment_status": "verified",
            "status": "delivered",
            "total": 1,
            "items": [{"product_id": "1", "name": "Forged", "price": 1}],
        },
    )
    assert response.status_code == 409
    assert db.query(OrderSnapshot).count() == 0
    assert db.query(OrderSnapshotItem).count() == 0


def test_legacy_sync_only_returns_owned_server_order(
    client, auth_headers, admin_headers, verified_user, db
):
    product = db.query(Product).first()
    stock = product.stock_quantity
    order = main.upsert_local_order_snapshot(
        db,
        verified_user,
        OrderSyncRequest(
            order_reference="order_server_test",
            source="razorpay_checkout",
            status="placed",
            payment_status="verified",
            total=1234,
            items=[
                {
                    "product_id": str(product.id),
                    "backend_product_id": product.id,
                    "name": product.name,
                    "qty": 1,
                    "price": 1234,
                }
            ],
        ),
    )
    db.commit()
    payload = {
        "order_reference": order.order_reference,
        "status": "delivered",
        "source": "android_app",
        "payment_status": "refunded",
        "total": 0,
        "items": [],
    }
    result = client.post("/orders/sync", headers=auth_headers, json=payload)
    assert result.status_code == 200
    assert result.json()["payment_status"] == "verified"
    assert result.json()["status"] == "placed"
    assert result.json()["total"] == 1234
    assert len(result.json()["items"]) == 1
    assert client.post("/orders/sync", headers=admin_headers, json=payload).status_code == 409
    assert client.post("/orders/sync", json=payload).status_code == 401
    db.refresh(order)
    db.refresh(product)
    assert order.source == "razorpay_checkout" and product.stock_quantity == stock
