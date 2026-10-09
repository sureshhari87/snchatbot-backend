"""Checkout and verify/webhook races use disposable local PostgreSQL only."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

from test_payment_concurrency_postgres import local_postgres as _pg
from test_savings import START
from test_savings_concurrency_postgres import setup

from models import User, utc_now
from savings import SavingsConflict, balance, verify_razorpay
from savings_api import process_webhook
from savings_checkout import checkout, reconcile
from savings_models import SavingsCheckoutAttempt, SavingsEntry, SavingsPayment

local_postgres = _pg


def prepare(factory, label):
    user_id, scheme_id, payment_id = setup(factory, label)
    with factory() as db:
        db.get(SavingsPayment, payment_id).mode = "razorpay"
        db.commit()
    return user_id, scheme_id, payment_id


def test_parallel_checkout_posts_to_provider_once(local_postgres):
    user_id, scheme_id, payment_id = prepare(local_postgres, "checkout_race")
    start = Barrier(2)
    peer_seen = Event()
    calls = []

    def provider(method, path, payload=None):
        calls.append((method, path))
        assert peer_seen.wait(timeout=20)
        return 200, dict(payload, id="order_checkout_race", entity="order", status="created")

    def work(index):
        with local_postgres() as db:
            start.wait(timeout=20)
            try:
                row = checkout(db, payment_id, user_id, provider, START)
                db.commit()
                return row.provider_order_id
            except SavingsConflict:
                db.rollback()
                peer_seen.set()
                return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(work, range(2)))
    assert sorted(result) == ["blocked", "order_checkout_race"]
    assert calls == [("POST", "orders")]
    with local_postgres() as db:
        assert db.get(SavingsCheckoutAttempt, payment_id).state == "ready"
        assert balance(db, scheme_id) == (0, 0, 0)


def test_late_timeout_cannot_overwrite_recovered_binding(local_postgres):
    user_id, scheme_id, payment_id = prepare(local_postgres, "late_checkout_timeout")

    def provider(method, path, payload=None):
        order = dict(payload, id="order_late_checkout", entity="order", status="created")
        with local_postgres() as recovery:
            admin = recovery.get(User, user_id)
            reconcile(recovery, payment_id, admin, order["id"], lambda *args: (200, order), START)
            recovery.commit()
        raise RuntimeError("synthetic timeout after recovery")

    with local_postgres() as db:
        row = checkout(db, payment_id, user_id, provider, START)
        db.commit()
        assert row.provider_order_id == "order_late_checkout"
        assert db.get(SavingsCheckoutAttempt, payment_id).state == "ready"


def test_verify_and_webhook_post_one_credit(local_postgres):
    user_id, scheme_id, payment_id = prepare(local_postgres, "verify_webhook_race")
    with local_postgres() as db:
        payment = db.get(SavingsPayment, payment_id)
        payment.provider_order_id = "order_verify_webhook"
        db.commit()
    evidence = dict(
        id="pay_verify_webhook",
        order_id="order_verify_webhook",
        amount=50000,
        currency="INR",
        status="captured",
        amount_refunded=0,
        refunded=False,
    )
    provider = SimpleNamespace(fetch_razorpay_payment=lambda payment_id: (200, evidence))
    start = Barrier(2)

    def work(index):
        with local_postgres() as db:
            start.wait(timeout=20)
            if index == 0:
                entry = verify_razorpay(
                    db,
                    payment_id,
                    user_id,
                    evidence["id"],
                    provider.fetch_razorpay_payment,
                    utc_now,
                )
                db.commit()
                return entry.id
            return process_webhook(
                db,
                {"event": "payment.captured", "payload": {"payment": {"entity": evidence}}},
                provider,
            )["entry_id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(work, range(2)))) == 1
    with local_postgres() as db:
        assert balance(db, scheme_id) == (50000, 0, 0)
        assert db.query(SavingsEntry).filter_by(scheme_id=scheme_id).count() == 1


def test_ready_refresh_and_webhook_credit_once(local_postgres):
    from savings_checkout import refresh_checkout
    from savings_models import SavingsAudit

    user_id, scheme_id, payment_id = prepare(local_postgres, "ready_refresh_race")
    order = {}

    def create(method, path, payload=None):
        assert (method, path) == ("POST", "orders")
        order.update(payload, id="order_ready_refresh", entity="order", status="created")
        return 200, order

    with local_postgres() as db:
        checkout(db, payment_id, user_id, create, START)
        db.commit()
    order["status"] = "paid"
    evidence = dict(
        id="pay_ready_refresh",
        order_id=order["id"],
        amount=50000,
        currency="INR",
        status="captured",
        amount_refunded=0,
        refunded=False,
    )
    reads = []

    def provider_call(method, path, payload=None):
        reads.append((method, path))
        assert method == "GET"
        if path == "orders/" + order["id"]:
            return 200, order
        if path == "orders/" + order["id"] + "/payments":
            return 200, {"items": [evidence]}
        if path == "payments/" + evidence["id"]:
            return 200, evidence
        raise AssertionError("Unexpected provider path")

    provider = SimpleNamespace(fetch_razorpay_payment=lambda identity: (200, evidence))
    start = Barrier(2)

    def work(index):
        with local_postgres() as db:
            start.wait(timeout=20)
            if index == 0:
                refresh_checkout(db, payment_id, user_id, provider_call, utc_now())
                db.commit()
                return db.query(SavingsEntry).filter_by(payment_id=payment_id).one().id
            return process_webhook(
                db,
                {"event": "payment.captured", "payload": {"payment": {"entity": evidence}}},
                provider,
            )["entry_id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(work, range(2)))) == 1
    with local_postgres() as db:
        assert balance(db, scheme_id) == (50000, 0, 0)
        assert db.query(SavingsEntry).filter_by(payment_id=payment_id).count() == 1
        assert (
            db.query(SavingsAudit).filter_by(scheme_id=scheme_id, action="payment_posted").count()
            == 1
        )
        assert (
            db.query(SavingsAudit)
            .filter_by(scheme_id=scheme_id, action="checkout_recovered")
            .count()
            <= 1
        )
    assert reads and all(method == "GET" for method, _ in reads)
