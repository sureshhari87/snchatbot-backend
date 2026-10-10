"""Real reservation migration checks, using only synthetic local records."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from database import Base
from models import OrderSnapshot, User, utc_now
from rewards_vouchers_models import RewardAccount
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent
from rewards_vouchers_transactions import reserve


def migrations(connection, *, include_funding=False):
    result = []
    filenames = ["0027_rewards_vouchers_ledger.py", "0028_financial_reservations.py"]
    if include_funding:
        filenames.append("0029_voucher_funding.py")
    for filename in filenames:
        spec = importlib.util.spec_from_file_location(
            "financial_" + filename[:-3],
            Path(__file__).resolve().parents[1] / "alembic" / "versions" / filename,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        result.append(module)
    return result


@pytest.fixture
def migrated():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(connection, tables=[User.__table__, OrderSnapshot.__table__])
        revisions = migrations(connection)
        connection.commit()
        with Session(connection) as db:
            db.add(
                User(
                    id=1,
                    username="synthetic",
                    email="synthetic@example.invalid",
                    hashed_password="unused",
                )
            )
            db.add(
                RewardAccount(
                    user_id=1, balance_points=100, reserved_points=0, created_at=utc_now()
                )
            )
            db.commit()
            yield db, revisions
    engine.dispose()


def seed(db):
    row = reserve(
        db, user_id=1, request_key="request", merchandise_paise=30000, reward_points_requested=50
    )
    db.commit()
    return row.id


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE financial_reservations SET payable_paise=999 WHERE id=:id",
        "UPDATE financial_reservations SET quote_json='{}' WHERE id=:id",
        "UPDATE financial_reservations SET voucher_id=99 WHERE id=:id",
        "UPDATE financial_reservations SET request_key='other' WHERE id=:id",
        "DELETE FROM financial_reservations WHERE id=:id",
        "UPDATE financial_reservation_events SET evidence_sha256='changed' WHERE reservation_id=:id",
        "DELETE FROM financial_reservation_events WHERE reservation_id=:id",
    ],
)
def test_terms_and_audit_cannot_be_mutated(migrated, sql):
    db, _ = migrated
    row_id = seed(db)
    with pytest.raises(DatabaseError):
        db.execute(text(sql), dict(id=row_id))
    db.rollback()
    assert db.get(FinancialReservation, row_id).payable_paise == 25000
    assert db.query(FinancialReservationEvent).count() == 1


def test_real_migration_accepts_posting_and_rejects_reverse_or_rebind(migrated):
    from test_rewards_vouchers_transactions import ready

    from rewards_vouchers_transactions import settle

    db, _ = migrated
    row, order, payment = ready(db)
    settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    db.commit()
    row_id = row.id
    for sql in (
        "UPDATE financial_reservations SET state='ready',provider_payment_id=NULL WHERE id=:id",
        "UPDATE financial_reservations SET provider_order_id='order_Other' WHERE id=:id",
        "UPDATE financial_reservations SET provider_payment_id='pay_Other' WHERE id=:id",
    ):
        with pytest.raises(DatabaseError):
            db.execute(text(sql), dict(id=row_id))
        db.rollback()
    assert db.get(FinancialReservation, row_id).state == "posted"
    assert db.query(FinancialReservationEvent).count() == 3


def test_populated_reservation_downgrade_preserves_records(migrated):
    db, revisions = migrated
    row_id = seed(db)
    with pytest.raises(RuntimeError, match="preservation plan"):
        revisions[-1].downgrade()
    assert db.get(FinancialReservation, row_id) is not None


def test_empty_reservation_revision_can_be_downgraded(migrated):
    db, revisions = migrated
    revisions[-1].downgrade()
    assert db.execute(text("SELECT count(*) FROM reward_accounts")).scalar_one() == 1


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE gift_vouchers SET amount_paise=30000 WHERE id=:id",
        "UPDATE gift_vouchers SET code_hash='other' WHERE id=:id",
        "UPDATE gift_vouchers SET assigned_user_id=1 WHERE id=:id",
        "UPDATE gift_vouchers SET expires_at='2030-01-01' WHERE id=:id",
        "DELETE FROM gift_vouchers WHERE id=:id",
    ],
)
def test_funded_voucher_identity_and_validity_are_frozen(migrated, sql):
    from test_rewards_vouchers_transactions import voucher

    from rewards_vouchers_models import GiftVoucher

    db, _ = migrated
    item = voucher(db)
    db.commit()
    row_id = item.id
    with pytest.raises(DatabaseError):
        db.execute(text(sql), dict(id=row_id))
    db.rollback()
    assert db.get(GiftVoucher, row_id).amount_paise == 20000
