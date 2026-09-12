from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import main


@pytest.mark.parametrize("value,expected", [(None, 0), ("", 0), ("bad", 0), ({}, 0), ("2.5", 2.5)])
def test_numeric_product_values(value, expected):
    assert main.firestore_to_float(value) == expected
    assert main.firestore_to_int(value) == int(expected)


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("available", True),
        ("inactive", False),
        ("unknown", None),
    ],
)
def test_legacy_boolean_values(value, expected):
    assert main.firestore_to_bool(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [("24KT", "24K"), ("22KT", "22K"), ("18KT", "18K"), ("80% HUID", "800"), (None, "22K")],
)
def test_purity_aliases(value, expected):
    assert main.firestore_normalize_purity(value) == expected


@pytest.mark.parametrize(
    "metal,purity,rates,expected",
    [
        ("silver", "925", {"silver": 100}, 100),
        ("gold", "18K", {"gold18k": 700}, 700),
        ("gold", "18K", {"gold24k": 1000}, 750),
        ("gold", "18K", {"gold22k": 1100}, 900),
        ("gold", "18K", {}, 25),
        ("gold", "24K", {"gold24k": 1000}, 1000),
        ("gold", "22K", {"gold22k": 900}, 900),
    ],
)
def test_metal_rate_conversion(metal, purity, rates, expected):
    assert main.firestore_metal_rate(metal, purity, rates, 25) == expected


def test_legacy_pricing_includes_explicit_charges_and_tax():
    product = {
        "metal": "Gold",
        "weight": 2,
        "purity": "22KT",
        "price": 500,
        "wastagePercent": 10,
        "makingCharge": 100,
        "stoneCharge": 50,
        "diamondCharge": 20,
        "shippingCharge": 30,
        "gstPercent": 5,
    }
    assert main.firestore_product_price(product, {"gold22k": 1000}) == 2520
    product["useDynamicPricing"] = False
    assert main.firestore_product_price(product, {"gold22k": 1000}) == 500
    assert main.firestore_product_price({"metal": "gold", "weight": 2}, {}) == 0
    assert main.firestore_normalize_metal("Sterling Silver") == "silver"
    assert main.firestore_normalize_metal("Platinum") == "platinum"


def test_legacy_stock_active_and_missing_aliases():
    assert main.firestore_first_value({"a": " ", "b": 0}, ["missing", "a", "b"]) == 0
    assert main.firestore_first_value({}, ["missing"]) is None
    assert main.firestore_product_stock({"stockQuantity": "3"}) == 3
    assert main.firestore_product_active({}) is True
    assert main.firestore_product_active({"isActive": False}) is False
    assert main.firestore_product_active({"deleted": True}) is False


def test_timestamp_expiry_and_absent_snapshots():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert main.firestore_to_datetime(SimpleNamespace(to_datetime=lambda: past)) == past
    assert main.firestore_is_expired(past) is True
    assert main.firestore_is_expired(future) is False
    assert main.firestore_is_expired(None) is False
    assert main.firestore_to_datetime("invalid") is None
    assert main.firestore_snapshot_dict(None) == {}
    assert main.firestore_snapshot_dict(SimpleNamespace(exists=True, to_dict=lambda: [])) == {}
    assert main.firestore_snapshot_dict(
        SimpleNamespace(exists=True, to_dict=lambda: {"id": 1})
    ) == {"id": 1}
    assert main.firestore_document_id(SimpleNamespace(id="abc"), "fallback") == "abc"
    assert main.firestore_document_id(None, "fallback") == "fallback"
