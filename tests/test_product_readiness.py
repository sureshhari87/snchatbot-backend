"""Approved readiness rules, tested without external services."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from commerce import DeliveryRequest, delivery_quote, validate_checkout
from models import AppConfigEntry, Product
from product_readiness import readiness

BASE = {
    "readyToShip": True,
    "madeToOrder": False,
    "handlingDays": 0,
    "manufacturingDays": 0,
    "sizeAdjustmentDays": 0,
}
NOW = datetime(2026, 10, 12, 8, tzinfo=timezone.utc)  # Monday before cutoff


@pytest.fixture
def prepared(db):
    product = db.query(Product).filter(Product.in_stock.is_(True)).first()
    product.source_data = dict(BASE)
    db.add(
        AppConfigEntry(
            key="commerce.delivery_routes",
            value=json.dumps(
                [
                    {
                        "pin_prefix": "625009",
                        "active": True,
                        "method": "standard",
                        "handling_days": 2,
                        "minimum_transit_days": 1,
                        "maximum_transit_days": 2,
                        "working_weekdays": [0, 1, 2, 3, 4],
                        "holidays": [],
                        "cutoff": "16:00",
                        "fulfilment_group": "synthetic",
                    }
                ]
            ),
            is_public=False,
        )
    )
    db.commit()
    return product


def quote(db, product, size=""):
    return delivery_quote(
        db,
        DeliveryRequest(
            product_id=product.id,
            destination_pin="625009",
            selected_size=size,
        ),
        now=NOW,
    )


@pytest.mark.parametrize("handling,expected", [(1, "2026-10-14"), (3, "2026-10-15")])
def test_larger_handling_wins(db, prepared, handling, expected):
    prepared.source_data = {**BASE, "handlingDays": handling}
    assert quote(db, prepared)["estimatedDispatchDate"] == expected


def test_manufacturing_and_selected_size_adjustment_add_business_days(db, prepared):
    prepared.source_data = {
        **BASE,
        "readyToShip": False,
        "madeToOrder": True,
        "manufacturingDays": 2,
        "sizeAdjustmentDays": 1,
        "sizes": ["12", "14"],
        "sizeAdjustmentSizes": ["14"],
    }
    adjusted = quote(db, prepared, "14")
    regular = quote(db, prepared, "12")
    assert adjusted["estimatedDispatchDate"] == "2026-10-19"
    assert regular["estimatedDispatchDate"] == "2026-10-16"
    assert adjusted["madeToOrder"] is True


@pytest.mark.parametrize(
    "earliest",
    [
        "2026-10-17",
        "2026-10-16T20:00:00Z",
    ],
)
def test_earliest_dispatch_uses_ist_and_rolls_forward_weekend(db, prepared, earliest):
    prepared.source_data = {**BASE, "dispatchNotBefore": earliest}
    result = quote(db, prepared)
    assert result["estimatedDispatchDate"] == "2026-10-19"
    assert result["earliestDeliveryDate"] == "2026-10-20"


def test_earliest_date_never_shortens_preparation(db, prepared):
    prepared.source_data = {**BASE, "dispatchNotBefore": "2026-10-01"}
    assert quote(db, prepared)["estimatedDispatchDate"] == "2026-10-14"


@pytest.mark.parametrize(
    "change",
    [
        {"readyToShip": None},
        {"readyToShip": True, "madeToOrder": True},
        {"readyToShip": False},
        {"manufacturingDays": 1},
        {"handlingDays": True},
        {"handlingDays": -1},
        {"handlingDays": 366},
        {"handlingDays": "1"},
        {"manufacturingDays": None},
        {"dispatchNotBefore": "2026-02-30"},
        {"dispatchNotBefore": "2026-10-17T12:00:00"},
        {"dispatchNotBefore": ""},
        {"sizeAdjustmentDays": 1},
        {"sizeAdjustmentDays": 1, "sizes": ["12"], "sizeAdjustmentSizes": ["99"]},
    ],
)
def test_unsafe_readiness_requires_review(db, prepared, change):
    prepared.source_data = {**BASE, **change}
    with pytest.raises(HTTPException) as error:
        quote(db, prepared)
    assert error.value.status_code == 409
    assert error.value.detail == "Product readiness requires admin review"


def test_missing_metadata_is_not_immediate_readiness(db, prepared):
    prepared.source_data = {}
    with pytest.raises(HTTPException, match="admin review"):
        quote(db, prepared)


def test_adjustment_requires_explicit_selected_size():
    data = {**BASE, "sizeAdjustmentDays": 1, "sizes": ["12"], "sizeAdjustmentSizes": ["12"]}
    with pytest.raises(ValueError):
        readiness(data)


def test_public_quote_and_checkout_both_reject_missing_readiness(
    client,
    auth_headers,
    db,
    prepared,
):
    prepared.source_data = {}
    db.commit()
    response = client.post(
        "/delivery/quote",
        headers=auth_headers,
        json={
            "product_id": prepared.id,
            "destination_pin": "625009",
        },
    )
    assert response.status_code == 409
    item = SimpleNamespace(
        backend_product_id=prepared.id,
        product_id=str(prepared.id),
        qty=1,
        selected_size="",
        selected_variant="",
        cart_item_id=None,
    )
    payload = SimpleNamespace(
        items=[item],
        coupon_code=None,
        gift_voucher_code=None,
        reward_points_requested=0,
        delivery_address={"postal_code": "625009"},
    )
    with pytest.raises(HTTPException, match="admin review"):
        validate_checkout(db, payload, SimpleNamespace(id=1))


def test_checkout_keeps_size_specific_promises_and_aggregate_stock(db, prepared):
    prepared.stock_quantity = 2
    prepared.source_data = {
        **BASE,
        "sizes": ["12", "14"],
        "sizeAdjustmentDays": 1,
        "sizeAdjustmentSizes": ["14"],
    }

    def item(size):
        return SimpleNamespace(
            backend_product_id=prepared.id,
            product_id=str(prepared.id),
            qty=1,
            selected_size=size,
            selected_variant="",
            cart_item_id=None,
        )

    payload = SimpleNamespace(
        items=[item("12"), item("14")],
        coupon_code=None,
        gift_voucher_code=None,
        reward_points_requested=0,
        delivery_address={"postal_code": "625009"},
    )
    result = validate_checkout(db, payload, SimpleNamespace(id=1))
    assert len(result["delivery_promises"]) == 2
    dates = [row["dispatchEstimate"] for row in result["delivery_promises"]]
    assert dates[0] < dates[1]
    prepared.stock_quantity = 1
    with pytest.raises(HTTPException, match="stock"):
        validate_checkout(db, payload, SimpleNamespace(id=1))


def test_unrepresentable_delivery_date_fails_closed(db, prepared):
    prepared.source_data = {**BASE, "dispatchNotBefore": "9999-12-31"}
    with pytest.raises(HTTPException, match="admin review") as error:
        quote(db, prepared)
    assert error.value.status_code == 409
