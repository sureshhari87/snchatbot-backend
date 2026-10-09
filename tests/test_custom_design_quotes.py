import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, text
from sqlalchemy.exc import DatabaseError

import custom_design_api
from custom_design_models import (
    CustomDesign,
    CustomDesignAudit,
    CustomDesignDecision,
    CustomDesignQuote,
)
from models import CustomOrderRequest


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(custom_design_api, "ENABLED", True)


def create(client, headers, **extra):
    body = dict(
        request_key="design-one",
        description="Engraved ring",
        metal="Gold",
        purity="22K",
        budget_range="25000-50000",
        customer_notes="Size 14",
    )
    body.update(extra)
    return client.post("/custom-designs", headers=headers, json=body)


def quote(client, headers, row, **extra):
    body = dict(
        request_key="quote-one", total_paise=2500000, advance_paise=500000, notes="Reviewed design"
    )
    body.update(extra)
    return client.post(f"/admin/custom-designs/{row['id']}/quotes", headers=headers, json=body)


def decide(client, headers, row, quoted, **extra):
    body = dict(request_key="decision-one", quote_id=quoted["id"], action="approved")
    body.update(extra)
    return client.post(f"/custom-designs/{row['id']}/decision", headers=headers, json=body)


def test_request_retry_is_one_parent_and_one_audit(client, auth_headers, db):
    first = create(client, auth_headers)
    assert first.status_code == 200
    assert create(client, auth_headers).json() == first.json()
    assert create(client, auth_headers, description="Different design").status_code == 409
    assert db.query(CustomDesign).count() == 1
    assert db.query(CustomOrderRequest).count() == 1
    assert db.query(CustomDesignAudit).count() == 1
    assert first.json()["advance_checkout_available"] is False


@pytest.mark.parametrize(
    "extra",
    [
        {"user_id": 999},
        {"status": "paid"},
        {"quote": {"total_paise": 1}},
        {"description": "  "},
        {"reference_image_url": "http://res.cloudinary.com/image"},
        {"reference_image_url": "https://private.example/image"},
        {"reference_image_url": "https://res.cloudinary.com:8443/image"},
    ],
)
def test_customer_cannot_supply_financial_state_or_unsafe_reference(client, auth_headers, extra):
    assert create(client, auth_headers, **extra).status_code == 422


@pytest.mark.parametrize(
    "extra",
    [
        {"total_paise": True},
        {"total_paise": 1.5},
        {"total_paise": 0},
        {"advance_paise": 2500001},
        {"advance_paise": 50},
        {"advance_paise": -1},
        {"currency": "USD"},
    ],
)
def test_quote_money_is_strict_integer_paise(client, auth_headers, admin_headers, extra):
    row = create(client, auth_headers).json()
    assert quote(client, admin_headers, row, **extra).status_code == 422


def test_quote_version_and_stale_acceptance(client, auth_headers, admin_headers, db):
    row = create(client, auth_headers).json()
    first = quote(client, admin_headers, row)
    assert first.status_code == 200
    q = first.json()
    assert quote(client, admin_headers, row).json() == q
    assert quote(client, admin_headers, row, total_paise=3000000).status_code == 409
    accepted = decide(client, auth_headers, row, q)
    assert accepted.status_code == 200 and accepted.json()["quote"]["state"] == "approved"
    second = quote(client, admin_headers, row, request_key="quote-two", total_paise=3000000).json()
    assert second["version"] == 2 and second["state"] == "sent"
    assert decide(client, auth_headers, row, q, request_key="stale").status_code == 409
    assert (
        decide(client, auth_headers, row, second, request_key="new", action="rejected").json()[
            "quote"
        ]["state"]
        == "rejected"
    )
    assert db.get(CustomDesignQuote, q["id"]).total_paise == 2500000
    assert db.query(CustomDesignQuote).count() == 2


def test_decision_retry_does_not_duplicate_history(client, auth_headers, admin_headers, db):
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row, advance_paise=0).json()
    first = decide(client, auth_headers, row, q)
    assert first.status_code == 200
    assert decide(client, auth_headers, row, q).json() == first.json()
    assert decide(client, auth_headers, row, q, action="rejected").status_code == 409
    assert db.query(CustomDesignDecision).count() == 1
    assert db.query(CustomDesignAudit).filter_by(action="quote_approved").count() == 1


def test_owner_and_admin_boundaries(client, auth_headers, admin_headers):
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row).json()
    assert quote(client, auth_headers, row).status_code == 403
    assert client.get(f"/custom-designs/{row['id']}", headers=admin_headers).status_code == 404
    assert decide(client, admin_headers, row, q).status_code == 404
    assert client.get("/custom-designs/my", headers=admin_headers).json() == []
    assert (
        client.get(f"/admin/custom-designs/{row['id']}/audit", headers=auth_headers).status_code
        == 403
    )
    assert client.get("/custom-designs/my").status_code == 401
    assert client.get("/admin/custom-designs", headers=admin_headers).status_code == 200
    assert client.get("/custom-designs/my?limit=51", headers=auth_headers).status_code == 422


def test_legacy_generic_status_cannot_bypass_structured_design(client, auth_headers, admin_headers):
    row = create(client, auth_headers).json()
    response = client.patch(
        f"/admin/custom-orders/{row['custom_order_id']}",
        headers=admin_headers,
        json={"status": "completed"},
    )
    assert response.status_code == 409


def test_disabled_feature_does_not_query_new_tables(
    client, auth_headers, admin_headers, db, monkeypatch
):
    monkeypatch.setattr(custom_design_api, "ENABLED", False)
    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement.lower())

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        assert create(client, auth_headers).status_code == 503
        assert client.get("/custom-designs/my", headers=auth_headers).status_code == 503
        assert client.get("/admin/custom-designs", headers=admin_headers).status_code == 503
        assert not any("custom_design" in stmt for stmt in statements)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)


def test_migration_preserves_immutable_history(
    client, auth_headers, admin_headers, db, monkeypatch
):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0025_custom_design_quotes.py"
    spec = importlib.util.spec_from_file_location("custom_design_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    module.downgrade()
    module.upgrade()
    db.commit()
    row = create(client, auth_headers).json()
    q = quote(client, admin_headers, row).json()
    assert decide(client, auth_headers, row, q).status_code == 200
    for table in module.HISTORY:
        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text(f"DELETE FROM {table}"))
        db.rollback()
        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text(f"UPDATE {table} SET id=id"))
        db.rollback()
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    with pytest.raises(RuntimeError, match="preservation"):
        module.downgrade()
