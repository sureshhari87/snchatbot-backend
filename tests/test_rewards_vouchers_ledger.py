"""Exercise the real unapplied migration against an isolated in-memory database."""

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session

from rewards_vouchers_models import (
    GiftVoucher,
    GiftVoucherEntry,
    RewardAccount,
    RewardEntry,
    RewardVoucherHold,
)


@pytest.fixture
def ledger():
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE order_snapshots (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO users VALUES (1)")
        conn.exec_driver_sql("INSERT INTO order_snapshots VALUES (1)")
        conn.commit()
        path = (
            Path(__file__).resolve().parents[1] / "alembic/versions/0027_rewards_vouchers_ledger.py"
        )
        spec = importlib.util.spec_from_file_location("financial_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        conn.commit()
        with Session(bind=conn) as session:
            yield session, migration
    engine.dispose()


def seed(session):
    now = datetime(2026, 10, 10)
    session.execute(
        insert(RewardAccount).values(
            user_id=1, balance_points=10, reserved_points=0, created_at=now
        )
    )
    session.execute(
        insert(GiftVoucher).values(
            id=1,
            purchaser_id=1,
            request_key="voucher-one",
            code_hash="a" * 64,
            source="purchased",
            amount_paise=50000,
            balance_paise=0,
            reserved_paise=0,
            state="pending",
            created_at=now,
        )
    )
    session.commit()
    return now


@pytest.mark.parametrize("balance,reserved", [(-1, 0), (0, -1), (1, 2)])
def test_invalid_reward_balances_rejected(ledger, balance, reserved):
    session, _ = ledger
    with pytest.raises(IntegrityError):
        session.execute(
            insert(RewardAccount).values(
                user_id=1,
                balance_points=balance,
                reserved_points=reserved,
                created_at=datetime(2026, 10, 10),
            )
        )
    session.rollback()


@pytest.mark.parametrize(
    "changes",
    [
        {"balance_paise": 1},
        {"reserved_paise": 1},
        {"amount_paise": 0},
        {"state": "active"},
        {"state": "active", "activated_at": datetime(2026, 10, 10)},
        {
            "state": "active",
            "activated_at": datetime(2026, 10, 10),
            "expires_at": datetime(2026, 10, 9),
        },
        {"source": "client"},
    ],
)
def test_unfunded_or_invalid_voucher_rejected(ledger, changes):
    session, _ = ledger
    values = dict(
        purchaser_id=1,
        request_key="one",
        code_hash="a" * 64,
        source="purchased",
        amount_paise=50000,
        balance_paise=0,
        reserved_paise=0,
        state="pending",
        created_at=datetime(2026, 10, 10),
    )
    values.update(changes)
    with pytest.raises(IntegrityError):
        session.execute(insert(GiftVoucher).values(**values))
    session.rollback()


def test_full_purchased_voucher_can_have_only_one_funding_entry(ledger):
    session, _ = ledger
    now = seed(session)
    base = dict(
        voucher_id=1,
        kind="manual_funding",
        amount_delta_paise=50000,
        actor_id=1,
        reason="Receipt verified",
        evidence_reference="receipt-one",
        evidence_sha256="a" * 64,
        created_at=now,
    )
    session.execute(insert(GiftVoucherEntry).values(event_key="fund-one", **base))
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(
            insert(GiftVoucherEntry).values(
                event_key="fund-two",
                **dict(base, kind="razorpay_funding", evidence_reference="pay_other"),
            )
        )
    session.rollback()


def test_reward_order_credit_is_idempotent_at_database_boundary(ledger):
    session, _ = ledger
    now = seed(session)
    base = dict(
        user_id=1,
        order_id=1,
        kind="purchase_credit",
        points_delta=1,
        evidence_reference="pay_one",
        evidence_sha256="a" * 64,
        created_at=now,
    )
    session.execute(insert(RewardEntry).values(event_key="earn-one", **base))
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(insert(RewardEntry).values(event_key="earn-two", **base))
    session.rollback()
    assert session.query(RewardEntry).count() == 1


@pytest.mark.parametrize(
    "kind,reason,actor",
    [
        ("manual_funding", None, None),
        ("manual_funding", "", 1),
        ("complimentary", " ", 1),
        ("complimentary", "Approved gift", None),
    ],
)
def test_manual_or_complimentary_credit_requires_actor_and_reason(ledger, kind, reason, actor):
    session, _ = ledger
    now = seed(session)
    with pytest.raises(IntegrityError):
        session.execute(
            insert(GiftVoucherEntry).values(
                voucher_id=1,
                event_key="credit",
                kind=kind,
                amount_delta_paise=50000,
                actor_id=actor,
                reason=reason,
                evidence_reference="receipt",
                evidence_sha256="a" * 64,
                created_at=now,
            )
        )
    session.rollback()


@pytest.mark.parametrize("target", [{}, {"reward_user_id": 1, "voucher_id": 1}])
def test_hold_must_have_exactly_one_target(ledger, target):
    session, _ = ledger
    now = seed(session)
    with pytest.raises(IntegrityError):
        session.execute(
            insert(RewardVoucherHold).values(
                **target,
                kind="admin_review",
                reference="review",
                reason="Audited review",
                actor_id=1,
                created_at=now,
            )
        )
    session.rollback()


def test_ledger_and_hold_history_cannot_be_rewritten_or_removed(ledger):
    session, migration = ledger
    now = seed(session)
    session.execute(
        insert(RewardEntry).values(
            user_id=1,
            order_id=1,
            event_key="earn",
            kind="purchase_credit",
            points_delta=1,
            evidence_reference="pay",
            evidence_sha256="a" * 64,
            created_at=now,
        )
    )
    session.execute(
        insert(GiftVoucherEntry).values(
            voucher_id=1,
            event_key="fund",
            kind="manual_funding",
            amount_delta_paise=50000,
            actor_id=1,
            reason="Verified receipt",
            evidence_reference="receipt",
            evidence_sha256="a" * 64,
            created_at=now,
        )
    )
    session.execute(
        insert(RewardVoucherHold).values(
            voucher_id=1,
            kind="admin_review",
            reference="review",
            reason="Audited review",
            actor_id=1,
            created_at=now,
        )
    )
    session.commit()
    for table in migration.HISTORY:
        for statement in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}"):
            with pytest.raises(DatabaseError, match="immutable"):
                session.execute(text(statement))
            session.rollback()
    with pytest.raises(RuntimeError, match="preservation"):
        migration.downgrade()


def test_empty_migration_can_be_reversed_without_touching_existing_tables(ledger):
    session, migration = ledger
    migration.downgrade()
    assert session.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
    assert session.execute(text("SELECT count(*) FROM order_snapshots")).scalar_one() == 1
    migration.upgrade()
    assert session.query(RewardEntry).count() == 0


def test_same_order_cannot_consume_one_voucher_twice(ledger):
    session, _ = ledger
    now = seed(session)
    base = dict(
        voucher_id=1,
        order_id=1,
        kind="redemption",
        amount_delta_paise=-100,
        evidence_reference="pay_first",
        evidence_sha256="a" * 64,
        created_at=now,
    )
    session.execute(insert(GiftVoucherEntry).values(event_key="spend-one", **base))
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(
            insert(GiftVoucherEntry).values(
                event_key="spend-two", **dict(base, evidence_reference="pay_other")
            )
        )
    session.rollback()
    assert session.query(GiftVoucherEntry).count() == 1
