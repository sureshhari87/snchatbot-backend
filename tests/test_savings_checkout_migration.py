import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from test_savings import START

from savings import enroll, payment_request
from savings_models import SavingsCheckoutAttempt


def migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0021_savings_checkout.py"
    spec = importlib.util.spec_from_file_location("checkout_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkout_schema_empty_roundtrip(test_engine, monkeypatch):
    module = migration()
    with test_engine.begin() as connection:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        module.downgrade()
        module.upgrade()
        assert "savings_checkout_attempts" in inspect(connection).get_table_names()
        assert {
            column["name"]
            for column in inspect(connection).get_columns("savings_checkout_attempts")
        } == {"payment_id", "receipt", "state", "created_at", "updated_at"}


def test_checkout_downgrade_preserves_unknown_outcome(db, verified_user, monkeypatch):
    scheme = enroll(db, verified_user.id, "monthly", "checkout-migration", START, 50000)
    payment = payment_request(
        db, verified_user.id, scheme.id, "checkout-migration", "razorpay", START
    )
    db.add(
        SavingsCheckoutAttempt(
            payment_id=payment.id,
            receipt="synthetic-receipt",
            state="unknown",
            created_at=START,
            updated_at=START,
        )
    )
    db.commit()
    module = migration()
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
    with pytest.raises(RuntimeError, match="preservation"):
        module.downgrade()
    assert db.get(SavingsCheckoutAttempt, payment.id).state == "unknown"
