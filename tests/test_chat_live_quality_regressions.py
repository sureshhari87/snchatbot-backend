import pytest

from main import filter_products, merge_filter_state
from models import Product


def item(db, name, kind, *, purity="22K", available=True):
    product = Product(
        name=name,
        category="Gold",
        product_type=kind,
        metal="Gold",
        purity=purity,
        price=180000,
        in_stock=available,
        stock_quantity=1 if available else 0,
    )
    db.add(product)
    db.commit()
    return product


def test_chat_default_excludes_sold_out(db):
    sold = item(db, "QA sold out bangle", "Bangle", available=False)
    rows, _ = filter_products(db, merge_filter_state({}, "gold bangles below 200000"))
    assert sold.id not in {row.id for row in rows}
    assert all(row.in_stock for row in rows)


def test_chat_purity_refinement_keeps_budget_and_type(db):
    wrong = item(db, "QA 22K bangle", "Bangle")
    right = item(db, "QA 18K bangle", "Bangle", purity="18K")
    first = merge_filter_state({}, "gold bangles between 1.5 lakh and 2.5 lakh")
    refined = merge_filter_state(first, "Only 18K please")
    assert refined["category"] == "Bangle"
    assert refined["min_price"] == 150000
    assert refined["max_price"] == 250000
    rows, _ = filter_products(db, refined)
    ids = {row.id for row in rows}
    assert right.id in ids
    assert wrong.id not in ids


@pytest.mark.parametrize(
    "query,wanted,other",
    [("gold bangles", "Bangle", "Bracelet"), ("gold chains", "Chain", "Necklace")],
)
def test_chat_types_are_not_substituted(db, query, wanted, other):
    right = item(db, "QA correct type", wanted)
    wrong = item(db, "QA different type", other)
    rows, _ = filter_products(db, merge_filter_state({}, query))
    ids = {row.id for row in rows}
    assert right.id in ids
    assert wrong.id not in ids


def test_customer_can_explicitly_view_sold_out(db):
    sold = item(db, "QA unavailable bangle", "Bangle", available=False)
    filters = merge_filter_state({}, "gold bangles include out of stock")
    assert filters["in_stock_only"] is False
    rows, _ = filter_products(db, filters)
    assert sold.id in {row.id for row in rows}
