import pytest

from catalogue_data import import_snapshot, price, product_values
from models import Product


def snapshot():
    prefix = "projects/sona-jewellery-app/databases/(default)/documents/"
    return {
        "project": "sona-jewellery-app",
        "database": "(default)",
        "collections": {
            "products": [
                {
                    "name": prefix + "products/old-id",
                    "fields": {
                        "name": {"stringValue": "Silver bowl"},
                        "metalType": {"stringValue": "silver"},
                        "metalWeight": {"integerValue": "10"},
                        "stock": {"integerValue": "4"},
                        "price": {"integerValue": "0"},
                        "useDynamicPricing": {"booleanValue": True},
                        "image": {
                            "stringValue": "https://res.cloudinary.com/store/image/upload/bowl.jpg"
                        },
                    },
                }
            ],
            "gold_rates": [
                {"name": prefix + "gold_rates/today", "fields": {"silver": {"integerValue": "100"}}}
            ],
        },
    }


def test_import_and_repeat_preserve_stock_and_identity(db):
    report = import_snapshot(db, snapshot())
    db.commit()
    product = db.query(Product).filter_by(source_id="old-id").one()
    assert report["inserted"] == 1
    assert product.price == 1030
    assert product.source_data["price"] == 0
    assert "cloudinary" in product.image
    product.stock_quantity = 2
    db.commit()
    repeated = import_snapshot(db, snapshot())
    db.commit()
    assert repeated["inserted"] == 0
    assert repeated["mapping"] == report["mapping"]
    assert product.stock_quantity == 2


def test_missing_stock_is_unavailable():
    result = product_values({"name": "Ring", "price": 100}, {})
    assert result["stock_quantity"] == 0
    assert result["in_stock"] is False


def test_imported_sizes_are_validated(client, db, auth_headers):
    import_snapshot(db, snapshot())
    product = db.query(Product).filter_by(source_id="old-id").one()
    product.source_data = {**product.source_data, "sizes": ["8", "9"]}
    db.commit()
    request = {"product_id": product.id, "quantity": 1}
    assert client.post("/cart", headers=auth_headers, json=request).status_code == 422
    assert (
        client.post(
            "/cart", headers=auth_headers, json={**request, "selected_size": "7"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/cart", headers=auth_headers, json={**request, "selected_size": "8"}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True])
def test_invalid_fixed_price_is_rejected(bad):
    with pytest.raises(ValueError):
        product_values({"name": "Ring", "price": bad}, {})


def test_dynamic_price_requires_rate_and_matches_legacy():
    import main

    data = {
        "name": "Ring",
        "metalType": "gold",
        "purity": "18K",
        "metalWeight": 2,
        "useDynamicPricing": True,
        "makingCharge": 200,
        "diamondCharge": 100,
        "stoneCharge": 50,
        "shippingCharge": 20,
        "wastagePercent": 5,
        "gstPercent": 3,
    }
    with pytest.raises(ValueError):
        price(data, {})
    rates = {"gold24k": 8000}
    assert price(data, rates) == pytest.approx(main.firestore_product_price(data, rates), abs=0.01)


def test_admin_routes_reject_non_admin(client, auth_headers):
    assert client.get("/admin/catalogue/products", headers=auth_headers).status_code == 403
    assert client.put("/admin/catalogue/rates", headers=auth_headers, json={}).status_code == 403


def test_admin_edit_and_rate_update_preserve_stock(client, db, admin_headers):
    import_snapshot(db, snapshot())
    db.commit()
    product = db.query(Product).filter_by(source_id="old-id").one()
    product.stock_quantity = 2
    db.commit()
    edit = client.patch(
        f"/admin/catalogue/products/{product.id}",
        headers=admin_headers,
        json={"description": "Updated", "images": ["https://res.cloudinary.com/store/bowl.jpg"]},
    )
    assert edit.status_code == 200, edit.text
    assert product.stock_quantity == 2
    rates = client.put(
        "/admin/catalogue/rates",
        headers=admin_headers,
        json={"silver": 200, "gold18k": 6000, "gold22k": 7000, "gold24k": 8000},
    )
    assert rates.status_code == 200, rates.text
    assert product.stock_quantity == 2
    assert product.price == 2060
    public = client.get(f"/products/{product.id}").json()
    assert public["price"] == 2060
    assert public["attributes"]["images"]
    assert "source_data" not in public
    invalid = client.put("/admin/catalogue/rates", headers=admin_headers, json={"silver": 0})
    assert invalid.status_code == 422
    assert product.price == 2060
