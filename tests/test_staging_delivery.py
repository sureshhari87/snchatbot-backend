"""Exact-PIN staging policy tests; isolated SQLite/mocks, never Neon."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from commerce import DeliveryRequest, delivery_quote
from models import AppConfigEntry, Product
from scripts.configure_staging_delivery import approved_routes, configure, validate_existing

URL = "postgresql://staging_owner:dummy@ep-example.neon.tech/snchatbot_staging?sslmode=require"


def install(db):
    db.add(
        AppConfigEntry(
            key="commerce.delivery_routes", value=json.dumps(approved_routes()), is_public=False
        )
    )
    db.commit()
    return db.query(Product).filter(Product.in_stock.is_(True)).first()


def test_only_approved_exact_pin_is_serviceable(client, auth_headers, db):
    product = install(db)
    for pin, expected in (("625009", 200), ("625008", 409), ("625010", 409), ("624401", 409)):
        response = client.post(
            "/delivery/quote",
            headers=auth_headers,
            json={"product_id": product.id, "destination_pin": pin},
        )
        assert response.status_code == expected
    assert (
        client.post(
            "/delivery/quote", json={"product_id": product.id, "destination_pin": "625009"}
        ).status_code
        == 401
    )


def test_approved_timing_and_cutoff(db):
    product = install(db)
    request = DeliveryRequest(product_id=product.id, destination_pin="625009")
    before = delivery_quote(db, request, now=datetime(2026, 9, 21, 9, tzinfo=timezone.utc))
    after = delivery_quote(db, request, now=datetime(2026, 9, 21, 12, tzinfo=timezone.utc))
    assert before["estimatedDispatchDate"] == "2026-09-23"
    assert before["earliestDeliveryDate"] == "2026-09-29"
    assert before["latestDeliveryDate"] == "2026-10-08"
    assert after["earliestDeliveryDate"] == "2026-09-30"
    assert after["latestDeliveryDate"] == "2026-10-09"


def test_exact_pin_does_not_bypass_stock_or_enable_express(client, auth_headers, db):
    product = install(db)
    payload = {"product_id": product.id, "destination_pin": "625009", "delivery_method": "express"}
    assert client.post("/delivery/quote", headers=auth_headers, json=payload).status_code == 409
    product.stock_quantity = 0
    db.commit()
    payload["delivery_method"] = "standard"
    assert client.post("/delivery/quote", headers=auth_headers, json=payload).status_code == 409


def test_repeated_setup_is_idempotent_and_preserves_other_rules():
    assert not validate_existing(None)
    assert not validate_existing("[]")
    assert validate_existing(json.dumps(approved_routes()))
    for value in ('[{"pin_prefix":"625"}]', "{}", "null", "not-json"):
        with pytest.raises(ValueError):
            validate_existing(value)


def test_wrong_host_or_production_target_never_connects():
    with patch("psycopg.connect") as connect:
        for url, host in (
            (URL, "wrong.neon.tech"),
            (URL.replace("snchatbot_staging", "neondb"), "ep-example.neon.tech"),
        ):
            with pytest.raises(ValueError):
                configure(url, host)
        connect.assert_not_called()


def test_existing_rules_are_not_overwritten():
    with (
        patch("scripts.configure_staging_delivery.preflight", return_value="ready"),
        patch("psycopg.connect") as connect,
    ):
        conn = connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.side_effect = [
            ("snchatbot_staging", "staging_owner"),
            ('[{"pin_prefix":"625"}]', False),
        ]
        with pytest.raises(ValueError):
            configure(URL, "ep-example.neon.tech")
        assert all(
            not call.args[0].startswith(("INSERT", "UPDATE", "DELETE"))
            for call in conn.execute.call_args_list
        )


@pytest.mark.parametrize("existing,write", [(None, "INSERT"), (("[]", False), "UPDATE")])
def test_configure_writes_only_approved_private_route(existing, write):
    with (
        patch("scripts.configure_staging_delivery.preflight", return_value="ready"),
        patch("psycopg.connect") as connect,
    ):
        conn = connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.side_effect = [
            ("snchatbot_staging", "staging_owner"),
            existing,
            (json.dumps(approved_routes()),),
        ]
        assert configure(URL, "ep-example.neon.tech") == "configured"
        writes = [
            c
            for c in conn.execute.call_args_list
            if c.args[0].startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        assert len(writes) == 1
        assert writes[0].args[0].startswith(write)
        assert json.dumps(approved_routes()) in writes[0].args[1]


def test_configure_repeated_run_does_not_write():
    with (
        patch("scripts.configure_staging_delivery.preflight", return_value="ready"),
        patch("psycopg.connect") as connect,
    ):
        conn = connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.side_effect = [
            ("snchatbot_staging", "staging_owner"),
            (json.dumps(approved_routes()), False),
        ]
        assert configure(URL, "ep-example.neon.tech") == "already_configured"
        assert all(
            not c.args[0].startswith(("INSERT", "UPDATE", "DELETE"))
            for c in conn.execute.call_args_list
        )
