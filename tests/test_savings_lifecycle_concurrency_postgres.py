"""Real row-lock races for request closure and audited holds, on disposable loopback PostgreSQL."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_savings import START
from test_savings_concurrency_postgres import local_postgres as local_postgres
from test_savings_concurrency_postgres import race, setup

from models import User
from savings import SavingsConflict, balance, confirm_manual, record_hold, redeem, resolve_pending
from savings_models import (
    SavingsAudit,
    SavingsEntry,
    SavingsHold,
    SavingsPayment,
    SavingsPaymentResolution,
)


def test_postgres_new_history_triggers(local_postgres, monkeypatch):
    with local_postgres() as db:
        path = Path(__file__).resolve().parents[1] / "alembic/versions/0024_savings_hold_review.py"
        spec = importlib.util.spec_from_file_location("review_dependency", path)
        review = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(review)
        monkeypatch.setattr(review, "op", Operations(MigrationContext.configure(db.connection())))
        review.downgrade()
        for revision in ("0022_savings_resolution", "0023_savings_holds"):
            path = Path(__file__).resolve().parents[1] / ("alembic/versions/" + revision + ".py")
            spec = importlib.util.spec_from_file_location(revision, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            monkeypatch.setattr(
                module, "op", Operations(MigrationContext.configure(db.connection()))
            )
            module.downgrade()
            module.upgrade()
            db.commit()
        monkeypatch.setattr(review, "op", Operations(MigrationContext.configure(db.connection())))
        review.upgrade()
        db.commit()
    user_id, scheme_id, payment_id = setup(local_postgres, "history_triggers")
    with local_postgres() as db:
        resolve_pending(
            db,
            payment_id,
            db.get(User, user_id),
            "Verified unpaid",
            START,
            kind="rejected",
            admin=True,
        )
        record_hold(db, scheme_id, "admin_review", "case", "Reviewed", START, actor_id=user_id)
        db.commit()
    for table in ("savings_payment_resolutions", "savings_holds"):
        for action in ("UPDATE " + table + " SET reason = 'changed'", "DELETE FROM " + table):
            with local_postgres() as db:
                with pytest.raises(DatabaseError, match="immutable"):
                    db.execute(text(action))
                db.rollback()


def test_rejection_races_confirmation_without_partial_credit(local_postgres):
    user_id, scheme_id, payment_id = setup(local_postgres, "close_vs_post")

    def work(index):
        with local_postgres() as db:
            try:
                actor = db.get(User, user_id)
                if index == 0:
                    confirm_manual(db, payment_id, actor, "race_receipt", START)
                    result = "posted"
                else:
                    resolve_pending(
                        db, payment_id, actor, "Verified unpaid", START, kind="rejected", admin=True
                    )
                    result = "rejected"
                db.commit()
                return result
            except SavingsConflict:
                db.rollback()
                return "conflict"

    results = race(work)
    assert results.count("conflict") == 1
    with local_postgres() as db:
        posted = db.get(SavingsPayment, payment_id).state == "posted"
        assert balance(db, scheme_id) == (50000 if posted else 0, 0, 0)
        assert db.query(SavingsEntry).filter_by(scheme_id=scheme_id).count() == int(posted)
        assert db.query(SavingsPaymentResolution).filter_by(payment_id=payment_id).count() == int(
            not posted
        )


@pytest.mark.parametrize("operation", ["posting", "redemption"])
def test_hold_races_financial_operation_serially(local_postgres, operation):
    user_id, scheme_id, payment_id = setup(local_postgres, "hold_vs_" + operation, "digigold")
    if operation == "redemption":
        with local_postgres() as db:
            confirm_manual(db, payment_id, db.get(User, user_id), "initial_receipt", START)
            db.commit()
    from datetime import timedelta

    now = START + timedelta(days=330) if operation == "redemption" else START

    def work(index):
        with local_postgres() as db:
            try:
                if index == 0:
                    record_hold(
                        db,
                        scheme_id,
                        "admin_review",
                        "race_case",
                        "Review required",
                        now,
                        actor_id=user_id,
                    )
                    result = "held"
                elif operation == "posting":
                    confirm_manual(db, payment_id, db.get(User, user_id), "hold_race_receipt", now)
                    result = "posted"
                else:
                    redeem(db, scheme_id, db.get(User, user_id), "hold_race_redemption", now)
                    result = "redeemed"
                db.commit()
                return result
            except SavingsConflict:
                db.rollback()
                return "blocked"

    results = race(work)
    assert results[0] == "held" and results[1] in ("blocked", "posted", "redeemed")
    with local_postgres() as db:
        expected_credit = (operation == "posting" and results[1] == "posted") or (
            operation == "redemption" and results[1] == "blocked"
        )
        assert balance(db, scheme_id) == ((10000, 14285, 714) if expected_credit else (0, 0, 0))
        assert db.query(SavingsHold).filter_by(scheme_id=scheme_id).count() == 1
        assert (
            db.query(SavingsAudit)
            .filter_by(scheme_id=scheme_id, action="review_hold_created")
            .count()
            == 1
        )
        with pytest.raises(SavingsConflict, match="held"):
            confirm_manual(db, payment_id, db.get(User, user_id), "later_receipt", now)
