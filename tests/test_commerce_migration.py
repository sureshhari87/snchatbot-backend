import hashlib
import hmac
import json

import main
from models import AppConfigEntry, CommerceCartItem, Product, User


def configure_delivery(db):
    db.add(
        AppConfigEntry(
            key="commerce.delivery_routes",
            value=json.dumps(
                [
                    {
                        "pin_prefix": "624",
                        "handling_days": 1,
                        "minimum_transit_days": 2,
                        "maximum_transit_days": 4,
                    }
                ]
            ),
            is_public=False,
        )
    )
    db.commit()


def test_catalogue_wishlist_cart_delivery_payment(client, auth_headers, db, monkeypatch):
    configure_delivery(db)
    product = db.query(Product).filter(Product.in_stock.is_(True)).first()
    stock = product.stock_quantity
    assert client.get(f"/products/{product.id}").json()["id"] == product.id
    assert (
        client.post("/wishlist", headers=auth_headers, json={"product_id": product.id}).status_code
        == 200
    )
    row = client.post(
        "/cart", headers=auth_headers, json={"product_id": product.id, "quantity": 1}
    ).json()
    assert row["product"]["id"] == product.id
    assert (
        client.post(
            "/delivery/quote",
            headers=auth_headers,
            json={
                "product_id": product.id,
                "destination_pin": "624401",
            },
        ).json()["serviceable"]
        is True
    )
    captured = {}

    def razorpay(method, path, payload=None):
        if method == "POST":
            captured.update(payload)
            return 200, {"id": "order_migration", **payload, "status": "created"}
        return 200, {
            "id": "pay_migration",
            "order_id": "order_migration",
            "status": "captured",
            "amount": captured["amount"],
            "currency": "INR",
        }

    monkeypatch.setattr(main, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(main, "RAZORPAY_KEY_SECRET", "test_secret")
    monkeypatch.setattr(main, "call_razorpay", razorpay)
    monkeypatch.setattr(main, "FIRESTORE_COMMERCE_ENABLED", True)
    response = client.post(
        "/payments/razorpay/orders",
        headers=auth_headers,
        json={
            "commerce_source": "fastapi",
            "delivery_address": {"postal_code": "624401"},
            "items": [
                {
                    "product_id": str(product.id),
                    "backend_product_id": product.id,
                    "qty": 1,
                    "cart_item_id": str(row["id"]),
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["server_calculated"] is True
    signature = hmac.new(
        b"test_secret", b"order_migration|pay_migration", hashlib.sha256
    ).hexdigest()
    verify = {
        "razorpay_order_id": "order_migration",
        "razorpay_payment_id": "pay_migration",
        "razorpay_signature": signature,
    }
    for _ in range(2):
        response = client.post("/payments/razorpay/verify", headers=auth_headers, json=verify)
        assert response.status_code == 200, response.text
    db.refresh(product)
    assert product.stock_quantity == stock - 1
    assert client.get("/cart", headers=auth_headers).json() == []


def test_cart_ownership_and_unconfigured_delivery(client, auth_headers, db):
    product = db.query(Product).first()
    other = User(username="other", email="other@example.com", hashed_password="unused")
    db.add(other)
    db.flush()
    row = CommerceCartItem(user_id=other.id, product_id=product.id, quantity=1, options={})
    db.add(row)
    db.commit()
    assert (
        client.patch(f"/cart/{row.id}", headers=auth_headers, json={"quantity": 2}).status_code
        == 404
    )
    assert client.delete(f"/cart/{row.id}", headers=auth_headers).status_code == 404
    assert client.get("/cart").status_code == 401
    response = client.post(
        "/delivery/quote",
        headers=auth_headers,
        json={"product_id": product.id, "destination_pin": "624401"},
    )
    assert response.status_code == 503
    assert "configured" in response.json()["detail"]


def test_stock_and_delivery_rejection(client, auth_headers, db):
    configure_delivery(db)
    product = db.query(Product).first()
    response = client.post(
        "/delivery/quote",
        headers=auth_headers,
        json={"product_id": product.id, "destination_pin": "110001"},
    )
    assert response.status_code == 409
    product.stock_quantity = 0
    db.commit()
    assert (
        client.post("/cart", headers=auth_headers, json={"product_id": product.id}).status_code
        == 409
    )
