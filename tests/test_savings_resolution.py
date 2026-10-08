import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError
from test_savings_api import (
    payment,
    scheme,
)
from test_savings_api import (
    provider as provider,
)
from test_savings_api import (
    savings_enabled as savings_enabled,
)

from models import utc_now
from savings_models import (
    SavingsAudit,
    SavingsCheckoutAttempt,
    SavingsEntry,
    SavingsPaymentResolution,
)


def cancel(client, headers, p, reason="unused checkout"):
    return client.post(
        f"/savings/payments/{p['id']}/cancel", headers=headers, json={"reason": reason}
    )


def reject(client, headers, p, **changes):
    return client.post(
        f"/admin/savings/payments/{p['id']}/reject",
        headers=headers,
        json=dict(reason="No receipt after store review", no_payment_received=True, **changes),
    )


def test_cancel_unused_is_idempotent_and_releases_slot(client, auth_headers, db, provider):
    row = scheme(client, auth_headers)
    p = payment(client, auth_headers, row, mode="razorpay")
    assert p["can_cancel"]
    first = cancel(client, auth_headers, p)
    assert first.status_code == 200
    assert first.json()["state"] == "cancelled" and first.json()["installment"] == 1
    assert cancel(client, auth_headers, p).json() == first.json()
    assert cancel(client, auth_headers, p, "changed").status_code == 409
    assert db.query(SavingsPaymentResolution).count() == 1
    assert db.query(SavingsAudit).filter_by(action="payment_cancelled").count() == 1
    assert db.query(SavingsEntry).count() == 0
    current = client.get(f"/savings/schemes/{row['id']}", headers=auth_headers).json()
    assert not current["pending_payment"] and current["principal_paise"] == 0
    assert payment(client, auth_headers, row, key="replacement")["installment"] == 1
    assert payment(client, auth_headers, row, mode="razorpay")["state"] == "cancelled"
    assert (
        client.post(f"/savings/payments/{p['id']}/checkout", headers=auth_headers).status_code
        == 409
    )
    assert provider[1] == []


def test_manual_requires_admin_no_receipt_review(client, auth_headers, admin_headers, db):
    p = payment(client, auth_headers, scheme(client, auth_headers))
    assert cancel(client, auth_headers, p).status_code == 409
    assert reject(client, auth_headers, p).status_code == 403
    path = f"/admin/savings/payments/{p['id']}/reject"
    for body in [
        {"reason": "review"},
        {"reason": "review", "no_payment_received": False},
        {"reason": "review", "no_payment_received": 1},
        {"reason": "", "no_payment_received": True},
        {"reason": "review", "no_payment_received": True, "balance": 0},
    ]:
        assert client.post(path, headers=admin_headers, json=body).status_code == 422
    assert reject(client, admin_headers, p).status_code == 200
    assert reject(client, admin_headers, p).status_code == 200
    assert (
        client.post(
            f"/admin/savings/payments/{p['id']}/confirm",
            headers=admin_headers,
            json={"reference": "late-receipt"},
        ).status_code
        == 409
    )
    assert db.query(SavingsEntry).count() == 0
    assert db.query(SavingsAudit).filter_by(action="payment_rejected").count() == 1


def test_cancellation_owner_isolation(client, auth_headers, admin_headers):
    p = payment(client, auth_headers, scheme(client, auth_headers), mode="razorpay")
    assert cancel(client, admin_headers, p).status_code == 409
    assert cancel(client, {}, p).status_code == 401


@pytest.mark.parametrize("state", ["creating", "unknown", "ready"])
def test_started_checkout_cannot_be_closed(client, auth_headers, admin_headers, db, state):
    p = payment(client, auth_headers, scheme(client, auth_headers), mode="razorpay")
    now = utc_now()
    db.add(
        SavingsCheckoutAttempt(
            payment_id=p["id"], receipt="synthetic", state=state, created_at=now, updated_at=now
        )
    )
    db.commit()
    assert cancel(client, auth_headers, p).status_code == 409
    assert reject(client, admin_headers, p).status_code == 409
    assert db.query(SavingsPaymentResolution).count() == 0


def test_posted_manual_cannot_be_rejected(client, auth_headers, admin_headers):
    p = payment(client, auth_headers, scheme(client, auth_headers))
    assert (
        client.post(
            f"/admin/savings/payments/{p['id']}/confirm",
            headers=admin_headers,
            json={"reference": "received"},
        ).status_code
        == 200
    )
    assert reject(client, admin_headers, p).status_code == 409


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0022_savings_resolution.py"
    spec = importlib.util.spec_from_file_location("resolution_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_roundtrip_and_immutable_history(client, auth_headers, db, monkeypatch):
    module = migration()
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    module.downgrade()
    module.upgrade()
    db.commit()
    assert "savings_payment_resolutions" in inspect(db.bind).get_table_names()
    p = payment(client, auth_headers, scheme(client, auth_headers), mode="razorpay")
    assert cancel(client, auth_headers, p).status_code == 200
    for statement in [
        "UPDATE savings_payment_resolutions SET reason = 'changed'",
        "DELETE FROM savings_payment_resolutions",
    ]:
        with pytest.raises(DatabaseError, match="immutable"):
            db.execute(text(statement))
        db.rollback()
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    with pytest.raises(RuntimeError, match="preservation"):
        module.downgrade()
    assert db.get(SavingsPaymentResolution, p["id"]).original_installment == 1
