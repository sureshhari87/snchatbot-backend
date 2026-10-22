import pytest

import main
from models import Product


@pytest.mark.parametrize("product_type", ["Ring", "Rings", None])
def test_material_category_matches_ring_type_without_matching_earrings(db, product_type):
    ring = Product(
        name="New Gold Ring",
        category="Gold",
        product_type=product_type,
        metal="Gold",
        price=19000,
        in_stock=True,
        stock_quantity=1,
    )
    earring = Product(
        name="Gold Earring",
        category="Gold",
        product_type=None,
        metal="Gold",
        price=18000,
        in_stock=True,
        stock_quantity=1,
    )
    db.add_all([ring, earring])
    db.commit()
    products, _ = main.filter_products(
        db,
        {
            "category": "Ring",
            "metal": "Gold",
            "max_price": 20000,
            "in_stock_only": True,
        },
        limit=50,
    )
    ids = {p.id for p in products}
    assert ring.id in ids
    assert earring.id not in ids
    assert all(p.price <= 20000 and p.in_stock for p in products)


def test_public_search_and_chat_share_material_category_compatibility(db, client):
    ring = Product(
        name="New Rose Ring",
        category="Gold",
        product_type="Ring",
        metal="Gold",
        price=19500,
        in_stock=True,
        stock_quantity=1,
    )
    db.add(ring)
    db.commit()
    response = client.get(
        "/products",
        params={
            "category": "Ring",
            "metal": "Gold",
            "max_price": 20000,
            "in_stock_only": True,
        },
    )
    assert response.status_code == 200
    assert ring.id in {p["id"] for p in response.json()}


def test_budget_typo_2ok_remains_a_twenty_thousand_cap():
    assert main.extract_budget_limit("rings below 2ok") == 20000


def test_compatibility_does_not_relax_budget_or_availability(db):
    products = [
        Product(
            name="Overpriced Gold Ring",
            category="Gold",
            product_type="Ring",
            metal="Gold",
            price=25000,
            in_stock=True,
            stock_quantity=1,
        ),
        Product(
            name="Sold Out Gold Ring",
            category="Gold",
            product_type="Ring",
            metal="Gold",
            price=15000,
            in_stock=False,
            stock_quantity=0,
        ),
    ]
    db.add_all(products)
    db.commit()
    matches, _ = main.filter_products(
        db,
        {
            "category": "Ring",
            "metal": "Gold",
            "max_price": 20000,
            "in_stock_only": True,
        },
        limit=50,
    )
    assert not ({p.id for p in products} & {p.id for p in matches})


def test_studio_budget_typo_keeps_the_same_cap():
    from sona_ai.engine import parse_query
    from sona_ai.models import SearchRequest

    assert parse_query(SearchRequest(query="rings below 2ok"))["max_price"] == 20000
