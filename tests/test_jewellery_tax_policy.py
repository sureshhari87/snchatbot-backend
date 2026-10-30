"""Finished-jewellery policy applies to writes/imports, with no blanket tax change."""

import pytest
from pydantic import ValidationError
from test_catalogue_bulk_import import csv_text, row

from billing_sales import SaleLine
from catalogue_bulk_data import plan
from catalogue_data import price, product_values
from jewellery_tax_policy import apply_jewellery_gst
from models import Product


@pytest.mark.parametrize(
    "category,metal,purity,key",
    [
        ("Gold", "gold", "22K", "gold22k"),
        ("Diamond", "gold", "18K", "gold18k"),
        ("Silver", "silver", "925", "silver"),
        ("Platinum", "platinum", "PT950", "platinum950"),
    ],
)
def test_manual_and_bulk_automatically_fill_same_approved_gst(category, metal, purity, key):
    data = dict(
        name="Synthetic ring",
        category=category,
        productType="Rings",
        metalType=metal,
        purity=purity,
        metalWeight=2,
        useDynamicPricing=True,
    )
    values = product_values(data, {key: 100})
    assert values["source_data"]["gstPercent"] == 3
    assert values["price"] == 206
    csv_row = row() | dict(
        category=category,
        productType="Rings",
        metalType=metal,
        purity=purity,
        metalWeight="2",
        useDynamicPricing="true",
        makingCharge="0",
        stoneCharge="0",
        diamondCharge="0",
        shippingCharge="0",
        wastagePercent="0",
        gstPercent="",
        goldColor="Yellow" if metal == "gold" else "",
    )
    products, _ = plan(csv_text(csv_row), "", {key: 100})
    assert products[0]["data"]["gstPercent"] == 3
    assert products[0]["price"] == "206.00"
    with pytest.raises(ValueError, match="fixed at 3"):
        plan(csv_text(csv_row | {"gstPercent": "18"}), "", {key: 100})


@pytest.mark.parametrize("value", [0, 5, 18, True, "NaN", "Infinity", -3])
def test_cannot_override_finished_jewellery_tax(value):
    with pytest.raises(ValueError, match="fixed at 3"):
        product_values(
            dict(name="Ring", category="Gold", productType="Rings", price=100, gstPercent=value), {}
        )
    with pytest.raises(ValueError, match="fixed at 3"):
        price(
            dict(
                category="Gold",
                productType="Rings",
                useDynamicPricing=True,
                metalType="gold",
                metalWeight=1,
                gstPercent=value,
            ),
            {"gold22k": 10000},
        )
    with pytest.raises(ValueError, match="fixed at 3"):
        apply_jewellery_gst(
            dict(category="Gold", productType="Rings", gstPercent=3, gst_percent=value)
        )


@pytest.mark.parametrize(
    "category,kind", [("Gifts", "Rings"), ("Gold", "Coin"), ("Silver", "Vessels"), ("Gold", "")]
)
def test_other_goods_keep_their_explicit_reviewed_tax(category, kind):
    data = dict(name="Other goods", category=category, productType=kind, price=100, gstPercent=18)
    assert product_values(data, {})["source_data"]["gstPercent"] == 18


def test_api_rejects_tax_override_without_changing_saved_product(client, db, admin_headers):
    rates = dict(gold18k=6000, gold22k=10000, gold24k=12000, silver=100)
    assert (
        client.put("/admin/catalogue/rates", headers=admin_headers, json=rates).status_code == 200
    )
    payload = dict(
        name="Synthetic bangle",
        category="Gold",
        productType="Bangles",
        metalType="gold",
        purity="22K",
        metalWeight=1,
        useDynamicPricing=True,
        makingCharge=1000,
        wastagePercent=10,
        shippingCharge=100,
        stock=2,
    )
    response = client.post("/admin/catalogue/products", headers=admin_headers, json=payload)
    assert response.status_code == 200
    identity = int(response.json()["id"])
    assert response.json()["data"]["gstPercent"] == 3
    saved = db.get(Product, identity)
    assert saved.price == 12463
    previous = dict(saved.source_data)
    for changes in [dict(gstPercent=18), dict(gst_percent=0)]:
        result = client.patch(
            f"/admin/catalogue/products/{identity}", headers=admin_headers, json=changes
        )
        assert result.status_code == 422
        db.expire_all()
        saved = db.get(Product, identity)
        assert saved.price == 12463 and saved.source_data == previous and saved.stock_quantity == 2


def test_invoice_jewellery_uses_approved_rate_and_heading():
    line = dict(
        source_item_id=1,
        description="Ring",
        hsn="7113",
        quantity=1,
        uqc="NOS",
        material="Gold",
        purity="22K",
        huids=["ABC123"],
        gross_weight_micrograms_each=1000000,
        net_weight_micrograms_each=1000000,
        metal_rate_paise_per_gram=10000,
        gst_basis_points=300,
    )
    assert SaleLine(**line).gst_basis_points == 300
    for changes in [dict(gst_basis_points=0), dict(gst_basis_points=1800), dict(hsn="7117")]:
        with pytest.raises(ValidationError):
            SaleLine(**(line | changes))


def test_rate_recalculation_rejects_legacy_tax_conflict_atomically(client, db, admin_headers):
    rates = dict(gold18k=6000, gold22k=10000, gold24k=12000, silver=100)
    assert (
        client.put("/admin/catalogue/rates", headers=admin_headers, json=rates).status_code == 200
    )
    payload = dict(
        name="Synthetic legacy tax conflict",
        category="Gold",
        productType="Bangles",
        metalType="gold",
        purity="22K",
        metalWeight=1,
        useDynamicPricing=True,
        makingCharge=1000,
        wastagePercent=10,
        shippingCharge=100,
        stock=2,
    )
    response = client.post("/admin/catalogue/products", headers=admin_headers, json=payload)
    assert response.status_code == 200
    identity = int(response.json()["id"])
    saved = db.get(Product, identity)
    saved.source_data = dict(saved.source_data, gstPercent=18)
    db.commit()
    before_rates = client.get("/admin/catalogue/rates", headers=admin_headers).json()
    next_rates = rates | {"gold22k": 11000}
    stopped = client.put("/admin/catalogue/rates", headers=admin_headers, json=next_rates)
    assert stopped.status_code == 422
    assert client.get("/admin/catalogue/rates", headers=admin_headers).json() == before_rates
    db.expire_all()
    saved = db.get(Product, identity)
    assert (
        saved.price == 12463 and saved.source_data["gstPercent"] == 18 and saved.stock_quantity == 2
    )
    # Only an explicit reviewed product edit resolves the legacy conflict.
    assert (
        client.patch(
            f"/admin/catalogue/products/{identity}", headers=admin_headers, json={"gstPercent": 3}
        ).status_code
        == 200
    )
    assert (
        client.put("/admin/catalogue/rates", headers=admin_headers, json=next_rates).status_code
        == 200
    )
    db.expire_all()
    saved = db.get(Product, identity)
    assert (
        saved.price == 13596 and saved.source_data["gstPercent"] == 3 and saved.stock_quantity == 2
    )
