import pytest

from catalogue_data import dynamic, price, product_values
from models import Product


@pytest.mark.parametrize("category", ["Gifts", "Gift Items", "gift"])
def test_gifts_ignore_metal_rates_even_with_stale_dynamic_metadata(category):
    data = {
        "name": "Gift",
        "category": category,
        "metalType": "gold",
        "purity": "22K",
        "metalWeight": 10,
        "useDynamicPricing": True,
        "price": 500,
    }
    assert not dynamic(data)
    assert price(data, {}) == 500
    assert price(data, {"gold22k": 99999}) == 500
    assert product_values(data, {})["source_data"]["useDynamicPricing"] is False


def test_platinum_requires_own_rate_and_supported_purity():
    data = {
        "name": "Band",
        "category": "Platinum",
        "metalType": "platinum",
        "purity": "PT950",
        "metalWeight": 2,
        "useDynamicPricing": True,
    }
    with pytest.raises(ValueError, match="platinum950"):
        price(data, {"gold22k": 7000, "silver": 100})
    assert price(data, {"platinum950": 3000}) == 6180
    with pytest.raises(ValueError, match="Pt950"):
        price({**data, "purity": "999"}, {"platinum950": 3000})


@pytest.mark.parametrize("purity,key", [("18K", "gold18k"), ("22K", "gold22k")])
def test_diamond_uses_selected_gold_purity_plus_diamond_charge(purity, key):
    data = {
        "name": "Diamond ring",
        "category": "Diamond",
        "metalType": "gold",
        "purity": purity,
        "metalWeight": 1,
        "diamondCharge": 1000,
        "useDynamicPricing": True,
    }
    assert price(data, {key: 6000}) == 7210


def test_huid_is_validated_without_fabricating_hallmark():
    data = product_values({"name": "Ring", "price": 500, "huid": "ab1234"}, {})
    assert data["source_data"]["huid"] == "AB1234"
    assert "bisHallmark" not in data["source_data"]
    with pytest.raises(ValueError, match="HUID"):
        product_values({"name": "Ring", "price": 500, "huid": "bad"}, {})


def test_rate_update_reprices_platinum_preserves_gifts_and_is_atomic(client, db, admin_headers):
    def create(data):
        response = client.post("/admin/catalogue/products", headers=admin_headers, json=data)
        assert response.status_code == 200
        return int(response.json()["id"])

    rates = {"gold18k": 6000, "gold22k": 7000, "gold24k": 8000, "silver": 100, "platinum950": 3000}
    assert (
        client.put("/admin/catalogue/rates", headers=admin_headers, json=rates).status_code == 200
    )
    pt = create(
        {
            "name": "Pt band",
            "category": "Platinum",
            "metalType": "platinum",
            "purity": "PT950",
            "metalWeight": 2,
            "useDynamicPricing": True,
            "stock": 3,
        }
    )
    gift = create(
        {
            "name": "Gift",
            "category": "Gifts",
            "metalType": "gold",
            "metalWeight": 10,
            "useDynamicPricing": True,
            "price": 500,
            "stock": 2,
        }
    )
    response = client.put(
        "/admin/catalogue/rates", headers=admin_headers, json={**rates, "platinum950": 4000}
    )
    assert response.status_code == 200
    assert db.get(Product, pt).price == 8240
    assert db.get(Product, gift).price == 500
    assert db.get(Product, pt).stock_quantity == 3
    # Older admin clients omit Pt950; preserve it instead of erasing the rate.
    legacy = {k: v for k, v in rates.items() if k != "platinum950"}
    assert (
        client.put("/admin/catalogue/rates", headers=admin_headers, json=legacy).status_code == 200
    )
    assert client.get("/catalogue/metal-rates").json()["platinum950"] == 4000
    assert (
        client.put(
            "/admin/catalogue/rates", headers=admin_headers, json={**rates, "platinum950": 0}
        ).status_code
        == 422
    )
    assert client.get("/catalogue/metal-rates").json()["platinum950"] == 4000
    assert db.get(Product, pt).price == 8240
