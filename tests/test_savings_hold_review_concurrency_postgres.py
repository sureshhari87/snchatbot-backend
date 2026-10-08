"""Audited release races on disposable loopback PostgreSQL only."""

import importlib.util
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_savings import START
from test_savings_concurrency_postgres import local_postgres as local_postgres
from test_savings_concurrency_postgres import race, setup

from models import User
from savings import active_holds, balance, post, record_hold
from savings_hold_review import release
from savings_models import SavingsCheckoutAttempt, SavingsHoldDecision, SavingsPayment


def prepared(factory, name):
    user_id, scheme_id, payment_id = setup(factory, name)
    payment_ref, order_ref, dispute_ref = (
        f"pay_{payment_id}",
        f"order_{payment_id}",
        f"disp_{payment_id}",
    )
    with factory() as db:
        payment = db.get(SavingsPayment, payment_id)
        payment.mode, payment.provider_order_id = "razorpay", order_ref
        db.add(
            SavingsCheckoutAttempt(
                payment_id=payment_id,
                receipt=f"receipt_{payment_id}",
                state="ready",
                created_at=START,
                updated_at=START,
            )
        )
        db.flush()
        post(db, payment_id, payment_ref, START, None, "razorpay")
        hold = record_hold(
            db,
            scheme_id,
            "dispute",
            dispute_ref,
            "Verified provider dispute",
            START,
            payment_id=payment_id,
        )
        db.commit()
        hold_id = hold.id
    captured = dict(
        id=payment_ref,
        entity="payment",
        order_id=order_ref,
        status="captured",
        captured=True,
        currency="INR",
        amount=50000,
        amount_refunded=0,
        refund_status=None,
    )
    dispute = dict(
        id=dispute_ref,
        entity="dispute",
        payment_id=payment_ref,
        status="won",
        currency="INR",
        amount=10000,
        amount_deducted=0,
    )

    def call(method, path):
        assert method == "GET"
        if path == "payments/" + payment_ref:
            return 200, captured
        if path == "disputes/" + dispute_ref:
            return 200, dispute
        if path.startswith("disputes?"):
            items = [dispute] if path.endswith("skip=0") else []
            return 200, dict(entity="collection", count=len(items), items=items)
        return 200, dict(entity="collection", count=0, items=[])

    return user_id, scheme_id, hold_id, dispute_ref, SimpleNamespace(call_razorpay=call)


def test_postgres_review_migration_roundtrip_and_immutable_history(local_postgres, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0024_savings_hold_review.py"
    spec = importlib.util.spec_from_file_location("review_pg_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with local_postgres() as db:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
        module.downgrade()
        module.upgrade()
        db.commit()
    user_id, scheme_id, hold_id, ref, provider = prepared(local_postgres, "release_immutable")
    with local_postgres() as db:
        release(
            db,
            hold_id,
            db.get(User, user_id),
            "Verified retained funds",
            "immutable-key",
            provider,
            START + timedelta(seconds=1),
        )
        db.commit()
    for command in (
        "UPDATE savings_hold_decisions SET reason='changed'",
        "DELETE FROM savings_hold_decisions",
    ):
        with local_postgres() as db:
            with pytest.raises(DatabaseError, match="immutable"):
                db.execute(text(command))
            db.rollback()
    with local_postgres() as db:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(db.connection())))
        with pytest.raises(RuntimeError, match="preservation"):
            module.downgrade()


def test_parallel_release_appends_one_decision(local_postgres):
    user_id, scheme_id, hold_id, ref, provider = prepared(local_postgres, "release_duplicate")

    def work(index):
        with local_postgres() as db:
            decision = release(
                db,
                hold_id,
                db.get(User, user_id),
                "Verified retained funds",
                "same-key",
                provider,
                START + timedelta(seconds=1),
            )
            db.commit()
            return decision.id

    assert len(set(race(work))) == 1
    with local_postgres() as db:
        assert active_holds(db, scheme_id).count() == 0
        assert db.query(SavingsHoldDecision).filter_by(hold_id=hold_id).count() == 1
        assert balance(db, scheme_id) == (50000, 0, 0)


def test_new_hold_races_release_without_escaping_review(local_postgres):
    user_id, scheme_id, hold_id, ref, provider = prepared(local_postgres, "release_new_hold")

    def work(index):
        with local_postgres() as db:
            if index == 0:
                release(
                    db,
                    hold_id,
                    db.get(User, user_id),
                    "Verified retained funds",
                    "new-hold-key",
                    provider,
                    START + timedelta(seconds=1),
                )
            else:
                record_hold(
                    db,
                    scheme_id,
                    "admin_review",
                    "new_case",
                    "New financial review",
                    START + timedelta(seconds=1),
                    actor_id=user_id,
                )
            db.commit()

    race(work)
    with local_postgres() as db:
        assert active_holds(db, scheme_id).count() == 1
        assert balance(db, scheme_id) == (50000, 0, 0)
