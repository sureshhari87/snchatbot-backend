"""Shopping constraints must never be relaxed to produce attractive results."""

import pytest

from sona_ai.engine import parse_query, search_catalog
from sona_ai.models import Product, SearchRequest


@pytest.mark.parametrize(
    "query,low,high",
    [
        ("gold rings below 2ok", None, 20000),
        ("rings budget 20k", None, 20000),
        ("rings up to 20 thousand", None, 20000),
        ("rings between 10 and 20k", 10000, 20000),
        ("rings between 10000 and 20000", 10000, 20000),
        ("chains between 1 lakh and 2 lakh", 100000, 200000),
        ("rings at least 15000", 15000, None),
    ],
)
def test_budget_language_keeps_both_bounds(query, low, high):
    filters = parse_query(SearchRequest(query=query))
    assert filters["min_price"] == low
    assert filters["max_price"] == high


def test_chain_is_not_substituted_by_necklace():
    products = [
        Product(id=1, name="Gold Chain", category="Chain", metal="Gold", price=15000),
        Product(id=2, name="Gold Necklace", category="Necklace", metal="Gold", price=15000),
    ]
    results, _ = search_catalog(SearchRequest(query="gold chains below 20k", products=products))
    assert [item.id for item in results] == [1]


def test_range_excludes_cheap_expensive_and_sold_out_options():
    products = [
        Product(id=i, name="Gold Ring", category="Ring", metal="Gold", price=price, in_stock=stock)
        for i, price, stock in [
            (1, 5000, True),
            (2, 15000, True),
            (3, 25000, True),
            (4, 15000, False),
        ]
    ]
    results, _ = search_catalog(
        SearchRequest(query="gold rings between 10 and 20k", products=products)
    )
    assert [item.id for item in results] == [2]


def test_explicit_filters_override_query_without_loosening_them():
    filters = parse_query(
        SearchRequest(query="rings between 10 and 20k", min_price=12000, max_price=18000)
    )
    assert filters["min_price"] == 12000
    assert filters["max_price"] == 18000


@pytest.mark.parametrize(
    "query,expected",
    [
        ("gold rings between 10 and 20k", (10000, 20000)),
        ("gold chains between 1 lakh and 2 lakh", (100000, 200000)),
        ("rings between 1.5 lakh and 2 lakh", (150000, 200000)),
    ],
)
def test_main_assistant_range_units(query, expected):
    import main

    assert main.extract_price_range(query) == expected


def test_main_assistant_decimal_lakh_budget():
    import main

    assert main.extract_budget_limit("gold rings below 1.5 lakh") == 150000
