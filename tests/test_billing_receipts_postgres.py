"""Real receipt races/migration protections on disposable loopback PostgreSQL only."""

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError
from test_billing_receipts import ISSUER, payment
from test_payment_concurrency_postgres import local_postgres as local_postgres
from test_savings_concurrency_postgres import race

import billing_receipts
from billing_models import BillingReceipt, BillingReceiptAudit
from models import User
from savings_models import SavingsEntry


def test_migration_concurrent_issue_and_preservation(local_postgres, monkeypatch):
    monkeypatch.setenv("BILLING_ISSUER_JSON", json.dumps(ISSUER))
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0037_billing_receipts.py"
    spec = importlib.util.spec_from_file_location("billing_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = local_postgres.kw["bind"]
    with engine.begin() as connection:
        BillingReceiptAudit.__table__.drop(connection)
        BillingReceipt.__table__.drop(connection)
        before = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        migration.downgrade()
        migration.upgrade()
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == before
    with local_postgres() as db:
        user = User(
            username="billing-race",
            email="billing-race@example.test",
            hashed_password="unused",
            is_verified=True,
            is_admin=True,
        )
        db.add(user)
        db.flush()
        row, _ = payment(db, user, user)
        owner, payment_id = user.id, row.id

    def work(index):
        with local_postgres() as db:
            result = billing_receipts.issue_savings(db, owner, payment_id)
            db.commit()
            return result["document"]["document_id"]

    identities = race(work)
    assert len(set(identities)) == 1
    with local_postgres() as db:
        assert db.query(BillingReceipt).count() == 1
        assert db.query(BillingReceiptAudit).count() == 1
        assert db.query(SavingsEntry).count() == 1
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        for statement in [
            "UPDATE billing_receipts SET snapshot_json='{}'",
            "DELETE FROM billing_receipts",
            "UPDATE billing_receipt_audit SET action='issued'",
            "DELETE FROM billing_receipt_audit",
        ]:
            with pytest.raises(DatabaseError), connection.begin_nested():
                connection.execute(text(statement))
        with pytest.raises(RuntimeError, match="preservation"):
            migration.downgrade()
        assert "billing_receipts" in inspect(connection).get_table_names()
        assert connection.execute(text("SELECT count(*) FROM billing_receipts")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM savings_entries")).scalar_one() == 1
