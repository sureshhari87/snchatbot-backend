"""Existing delivery rules stay unchanged; malformed active rules fail closed."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import commerce
from delivery_rules import project
from models import AppConfigEntry


def rule():
    return {
        "active": True,
        "pin_prefix": "625009",
        "method": "standard",
        "handling_days": 1,
        "minimum_transit_days": 2,
        "maximum_transit_days": 3,
        "working_weekdays": [0, 1, 2, 3, 4, 5],
        "holidays": [],
        "cutoff": "16:00",
        "fulfilment_group": "store",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"active": "false"},
        {"pin_prefix": ""},
        {"pin_prefix": "any"},
        {"handling_days": True},
        {"minimum_transit_days": 1.9},
        {"working_weekdays": [7]},
        {"working_weekdays": []},
        {"holidays": "2026-10-13"},
        {"holidays": ["2026-02-30"]},
        {"holidays": ["20261013"]},
        {"cutoff": "25:00"},
        {"maximum_transit_days": 1},
    ],
)
def test_invalid_active_rules_are_rejected(changes):
    with pytest.raises((ValueError, TypeError)):
        project({**rule(), **changes})


def test_inactive_incomplete_draft_never_grants_coverage():
    assert project({"active": False}) == {"active": False}


def test_projection_preserves_existing_effective_defaults():
    value = rule()
    for key in ["method", "working_weekdays", "holidays", "cutoff", "fulfilment_group"]:
        value.pop(key)
    value["handling_days"] = "1"
    assert project(value) == rule()


def test_admin_read_is_permission_scoped_and_excludes_other_settings(
    client, db, auth_headers, admin_headers
):
    raw = json.dumps([{**rule(), "internal_note": "SYNTHETIC-PRIVATE"}])
    entry = AppConfigEntry(key="commerce.delivery_routes", value=raw)
    db.add_all([entry, AppConfigEntry(key="private.synthetic", value="SYNTHETIC-OTHER")])
    db.commit()
    assert client.get("/admin/delivery-configuration").status_code == 401
    assert client.get("/admin/delivery-configuration", headers=auth_headers).status_code == 403
    response = client.get("/admin/delivery-configuration", headers=admin_headers)
    assert response.status_code == 200
    result = response.json()
    assert result["editable"] is False and result["weekday_base"] == 0
    assert result["routes"][0]["route"] == rule()
    assert "SYNTHETIC-PRIVATE" not in response.text
    assert "SYNTHETIC-OTHER" not in response.text
    assert (
        client.patch("/admin/delivery-configuration", headers=admin_headers, json={}).status_code
        == 405
    )
    db.refresh(entry)
    assert entry.value == raw
    assert db.query(AppConfigEntry).filter_by(key="private.synthetic").count() == 1


def test_quote_preserves_dates_and_skips_incomplete_inactive_draft(db, monkeypatch):
    db.add(
        AppConfigEntry(
            key="commerce.delivery_routes", value=json.dumps([{"active": False}, rule()])
        )
    )
    db.commit()
    monkeypatch.setattr(commerce, "product_or_error", lambda *args: object())
    monkeypatch.setattr(commerce, "check_stock", lambda *args: None)
    request = SimpleNamespace(
        product_id=1, quantity=1, destination_pin="625009", delivery_method="standard"
    )
    now = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
    result = commerce.delivery_quote(db, request, now)
    assert result["estimatedDispatchDate"] == "2026-10-14"
    assert result["earliestDeliveryDate"] == "2026-10-16"
    assert result["latestDeliveryDate"] == "2026-10-17"
    request.destination_pin = "999999"
    with pytest.raises(HTTPException) as error:
        commerce.delivery_quote(db, request, now)
    assert error.value.status_code == 409


def test_invalid_active_header_does_not_enable_broad_coverage(db, monkeypatch):
    db.add(
        AppConfigEntry(
            key="commerce.delivery_routes", value=json.dumps([{**rule(), "pin_prefix": ""}])
        )
    )
    db.commit()
    monkeypatch.setattr(commerce, "product_or_error", lambda *args: object())
    monkeypatch.setattr(commerce, "check_stock", lambda *args: None)
    request = SimpleNamespace(
        product_id=1, quantity=1, destination_pin="625009", delivery_method="standard"
    )
    with pytest.raises(HTTPException) as error:
        commerce.delivery_quote(db, request)
    assert error.value.status_code == 503
