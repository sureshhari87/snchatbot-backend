"""Opt-in disposable loopback PostgreSQL races; never contacts staging/Neon."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from test_payment_concurrency_postgres import local_postgres as _pg

from custom_design_api import (
    CreateInput,
    DecisionInput,
    QuoteInput,
    create,
    decide,
    document,
    locked,
    publish,
)
from custom_design_models import CustomDesignAudit, CustomDesignDecision, CustomDesignQuote
from custom_design_payment_api import event_operation
from custom_design_payments import CustomDesignAdvance, checkout, post_capture
from models import User

local_postgres = _pg


def setup(factory):
    with factory() as db:
        name = "custom_race_" + uuid4().hex
        user = User(
            username=name, email=name + "@example.test", hashed_password="unused", is_admin=True
        )
        db.add(user)
        db.flush()
        provider = SimpleNamespace(create_customer_action_lead=lambda *args, **kwargs: None)
        row = create(
            db,
            user,
            CreateInput(
                request_key="request",
                description="Synthetic ring",
                metal="Gold",
                purity="22K",
                budget_range="25000-50000",
            ),
            provider,
        )
        q = publish(
            db,
            row["id"],
            user,
            QuoteInput(request_key="quote", total_paise=2500000, advance_paise=500000),
        )
        decide(
            db,
            row["id"],
            user,
            DecisionInput(request_key="accept", quote_id=q["id"], action="approved"),
        )
        db.commit()
        return user.id, row["id"], q["id"]


def test_parallel_checkout_calls_provider_once(local_postgres):
    uid, did, qid = setup(local_postgres)
    barrier = Barrier(2)
    peer_seen = Event()
    calls = []

    def call(method, path, payload=None):
        calls.append((method, path))
        assert peer_seen.wait(timeout=20)
        return 200, dict(payload, id="order_parallelcustom", entity="order", status="created")

    provider = SimpleNamespace(call_razorpay=call)

    def work(index):
        with local_postgres() as db:
            barrier.wait(timeout=20)
            try:
                attempt, _ = checkout(db, did, uid, provider)
                db.commit()
                return attempt.provider_order_id
            except HTTPException as exc:
                assert exc.status_code == 409
                db.rollback()
                peer_seen.set()
                return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(work, range(2)))
    assert sorted(result) == ["blocked", "order_parallelcustom"]
    assert calls == [("POST", "orders")]


def test_quote_change_racing_checkout_never_charges_stale_terms(local_postgres):
    uid, did, qid = setup(local_postgres)
    barrier = Barrier(2)
    calls = []
    provider = SimpleNamespace(
        call_razorpay=lambda method, path, payload=None: (
            calls.append(method) or 200,
            dict(payload, id="order_quoterace", entity="order", status="created"),
        )
    )

    def work(index):
        with local_postgres() as db:
            barrier.wait(timeout=20)
            try:
                if index:
                    publish(
                        db,
                        did,
                        db.get(User, uid),
                        QuoteInput(request_key="new", total_paise=3000000, advance_paise=600000),
                    )
                    outcome = "changed"
                else:
                    checkout(db, did, uid, provider)
                    outcome = "checkout"
                db.commit()
                return outcome
            except HTTPException as exc:
                assert exc.status_code == 409
                db.rollback()
                return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(work, range(2)))
    assert "blocked" in result and len(set(result)) == 2
    with local_postgres() as db:
        current = document(locked(db, did), db)
        if "changed" in result:
            assert (
                current["quote"]["version"] == 2
                and current["quote"]["state"] == "sent"
                and calls == []
            )
        else:
            assert current["quote"]["id"] == qid and calls == ["POST"]


def test_quote_publication_racing_acceptance_never_accepts_new_terms(local_postgres):
    uid, did, qid = setup(local_postgres)
    barrier = Barrier(2)

    def work(index):
        with local_postgres() as db:
            barrier.wait(timeout=20)
            try:
                user = db.get(User, uid)
                if index:
                    publish(
                        db,
                        did,
                        user,
                        QuoteInput(request_key="new", total_paise=3000000, advance_paise=600000),
                    )
                else:
                    decide(
                        db,
                        did,
                        user,
                        DecisionInput(request_key="accept-again", quote_id=qid, action="approved"),
                    )
                db.commit()
                return "ok"
            except HTTPException as exc:
                assert exc.status_code == 409
                db.rollback()
                return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(work, range(2)))
    assert result[1] == "ok"
    with local_postgres() as db:
        current = document(locked(db, did), db)
        assert current["quote"]["version"] == 2 and current["quote"]["state"] == "sent"
        assert (
            db.query(CustomDesignDecision).filter_by(quote_id=current["quote"]["id"]).count() == 0
        )


def test_capture_and_webhook_race_post_one_advance(local_postgres):
    uid, did, qid = setup(local_postgres)
    order = {}

    def call(method, path, payload=None):
        if method == "POST":
            order.update(dict(payload, id="order_capturerace", entity="order", status="created"))
            return 200, dict(order)
        return 200, dict(
            id="pay_capturerace",
            entity="payment",
            order_id=order["id"],
            amount=500000,
            currency="INR",
            status="captured",
            captured=True,
            amount_refunded=0,
            refund_status=None,
        )

    provider = SimpleNamespace(call_razorpay=call)
    with local_postgres() as db:
        attempt, _ = checkout(db, did, uid, provider)
        db.commit()
    barrier = Barrier(2)

    def work(index):
        with local_postgres() as db:
            barrier.wait(timeout=20)
            if index:
                result = event_operation(
                    db,
                    {
                        "event": "payment.captured",
                        "payload": {
                            "payment": {
                                "entity": {
                                    "id": "pay_capturerace",
                                    "order_id": "order_capturerace",
                                }
                            }
                        },
                    },
                    provider,
                )["entry_id"]
            else:
                from custom_design_payments import CustomDesignCheckout

                row = locked(db, did, uid)
                result = post_capture(
                    db,
                    row,
                    db.get(CustomDesignQuote, qid),
                    db.get(CustomDesignCheckout, qid),
                    "pay_capturerace",
                    provider,
                ).id
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(work, range(2)))
    assert result[0] == result[1]
    with local_postgres() as db:
        assert db.query(CustomDesignAdvance).filter_by(design_id=did).count() == 1
        assert (
            db.query(CustomDesignAudit).filter_by(design_id=did, action="advance_posted").count()
            == 1
        )
