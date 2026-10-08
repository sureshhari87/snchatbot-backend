"""Synthetic savings races on disposable loopback-only PostgreSQL, never Neon."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError, IntegrityError
from test_payment_concurrency_postgres import local_postgres as _postgres_fixture
from test_savings import START
from test_savings_migration import load_migration

from models import User
from savings import SavingsConflict, balance, confirm_manual, enroll, payment_request, redeem
from savings_models import SavingsAudit, SavingsEntry, SavingsGoldRate

local_postgres = _postgres_fixture


def setup(factory, label, kind="monthly"):
    with factory() as db:
        user = User(
            username=label,
            email=label + "@example.test",
            hashed_password="unused",
            is_verified=True,
            is_admin=True,
        )
        db.add(user)
        db.flush()
        scheme = enroll(db, user.id, kind, label, START, 50000 if kind == "monthly" else None)
        if kind == "digigold":
            db.add(
                SavingsGoldRate(
                    paise_per_gram=700000,
                    source="synthetic",
                    created_by=user.id,
                    effective_at=START,
                    valid_until=START + timedelta(days=1000),
                )
            )
        db.flush()
        payment = payment_request(
            db, user.id, scheme.id, label, "manual", START, 10000 if kind == "digigold" else None
        )
        db.commit()
        return user.id, scheme.id, payment.id


def race(work):
    barrier = Barrier(2)

    def worker(index):
        barrier.wait(timeout=15)
        return work(index)

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(worker, range(2)))


def test_parallel_confirmation_posts_once(local_postgres):
    user_id, scheme_id, payment_id = setup(local_postgres, "savings_duplicate")

    def work(index):
        with local_postgres() as db:
            entry = confirm_manual(
                db, payment_id, db.get(User, user_id), "savings-duplicate-receipt", START
            )
            db.commit()
            return entry.id

    assert len(set(race(work))) == 1
    with local_postgres() as db:
        assert balance(db, scheme_id) == (50000, 0, 0)
        assert db.query(SavingsEntry).filter_by(scheme_id=scheme_id).count() == 1
        assert (
            db.query(SavingsAudit).filter_by(scheme_id=scheme_id, action="payment_posted").count()
            == 1
        )


def test_parallel_redemption_consumes_once(local_postgres):
    user_id, scheme_id, payment_id = setup(local_postgres, "savings_redemption", "digigold")
    with local_postgres() as db:
        confirm_manual(db, payment_id, db.get(User, user_id), "savings-redemption-payment", START)
        db.commit()

    def work(index):
        with local_postgres() as db:
            entry = redeem(
                db,
                scheme_id,
                db.get(User, user_id),
                "savings-redemption",
                START + timedelta(days=330),
            )
            db.commit()
            return entry.id

    assert len(set(race(work))) == 1
    with local_postgres() as db:
        assert balance(db, scheme_id) == (0, 0, 0)
        assert db.query(SavingsEntry).filter_by(scheme_id=scheme_id, kind="redemption").count() == 1


def test_parallel_installment_requests_allow_one_pending_slot(local_postgres):
    user_id, scheme_id, payment_id = setup(local_postgres, "savings_installment")
    with local_postgres() as db:
        confirm_manual(db, payment_id, db.get(User, user_id), "savings-installment-receipt", START)
        db.commit()

    def work(index):
        with local_postgres() as db:
            try:
                payment_request(db, user_id, scheme_id, f"next-{index}", "manual", START)
                db.commit()
                return "created"
            except SavingsConflict:
                db.rollback()
                return "conflict"

    assert sorted(race(work)) == ["conflict", "created"]


def test_same_receipt_cannot_credit_parallel_schemes(local_postgres):
    one = setup(local_postgres, "savings_shared_one")
    two = setup(local_postgres, "savings_shared_two")

    def work(index):
        user_id, _, payment_id = (one, two)[index]
        with local_postgres() as db:
            try:
                confirm_manual(
                    db, payment_id, db.get(User, user_id), "savings-shared-receipt", START
                )
                db.commit()
                return "posted"
            except (SavingsConflict, IntegrityError):
                db.rollback()
                return "conflict"

    assert sorted(race(work)) == ["conflict", "posted"]
    with local_postgres() as db:
        assert sum(balance(db, s[1])[0] for s in (one, two)) == 50000


def test_postgres_immutable_accounting_trigger(local_postgres, monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = load_migration()
    user_id, scheme_id, payment_id = setup(local_postgres, "savings_immutable", "digigold")
    with local_postgres() as db:
        confirm_manual(db, payment_id, db.get(User, user_id), "savings-immutable-receipt", START)
        db.commit()
        conn = db.connection()
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.install_immutable_tables()
        db.commit()
    for table in ("savings_entries", "savings_audit", "savings_gold_rates"):
        with local_postgres() as db:
            with pytest.raises(DatabaseError, match="immutable"):
                db.execute(text("UPDATE " + table + " SET id = id"))
            db.rollback()
    with local_postgres() as db:
        assert balance(db, scheme_id) == (10000, 14285, 714)


def test_parallel_enrollment_returns_one_scheme(local_postgres):
    with local_postgres() as db:
        user = User(
            username="savings_enroll_race",
            email="savings_enroll_race@example.test",
            hashed_password="unused",
            is_verified=True,
        )
        db.add(user)
        db.commit()
        user_id = user.id

    def work(index):
        with local_postgres() as db:
            row = enroll(db, user_id, "monthly", "same-enrollment", START, 50000)
            db.commit()
            return row.id

    assert len(set(race(work))) == 1


def test_final_posting_races_redemption_without_lost_value(local_postgres):
    user_id, scheme_id, first_id = setup(local_postgres, "savings_final_post")
    with local_postgres() as db:
        admin = db.get(User, user_id)
        confirm_manual(db, first_id, admin, "savings-final-0", START)
        for index in range(1, 10):
            row = payment_request(db, user_id, scheme_id, f"final-{index}", "manual", START)
            confirm_manual(db, row.id, admin, f"savings-final-{index}", START)
        last = payment_request(db, user_id, scheme_id, "final-10", "manual", START)
        db.commit()
        last_id = last.id
    mature = START.replace(month=12)

    def work(index):
        with local_postgres() as db:
            admin = db.get(User, user_id)
            try:
                if index == 0:
                    confirm_manual(db, last_id, admin, "savings-final-10", mature)
                    result = "posted"
                else:
                    redeem(db, scheme_id, admin, "savings-final-redemption", mature)
                    result = "redeemed"
                db.commit()
                return result
            except SavingsConflict:
                db.rollback()
                return "pending"

    result = race(work)
    assert result[0] == "posted"
    assert result[1] in ("pending", "redeemed")
    with local_postgres() as db:
        redeem(db, scheme_id, db.get(User, user_id), "savings-final-redemption", mature)
        db.commit()
        assert balance(db, scheme_id) == (0, 0, 0)
        redemption = db.query(SavingsEntry).filter_by(scheme_id=scheme_id, kind="redemption").one()
        assert redemption.principal_paise == -550000
        assert redemption.store_bonus_paise == 50000
