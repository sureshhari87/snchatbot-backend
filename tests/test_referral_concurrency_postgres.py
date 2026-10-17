"""Real referral races on the existing disposable loopback-only PostgreSQL harness."""

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from test_referrals import referral_migration
from test_rewards_vouchers_concurrency_postgres import (
    customers,
    parallel,
)
from test_rewards_vouchers_concurrency_postgres import (
    financial_postgres as _financial,
)
from test_rewards_vouchers_concurrency_postgres import (
    local_postgres as _local,
)

import referrals
from financial_checkout import hold_targets
from models import OrderSnapshot
from referral_models import ReferralApplication, ReferralCredit
from rewards_vouchers_models import RewardAccount, RewardVoucherHold
from rewards_vouchers_transactions import bind, reserve, settle

financial_postgres = _financial
local_postgres = _local


@pytest.fixture(scope="module")
def referral_postgres(financial_postgres):
    with financial_postgres.kw["bind"].begin() as connection:
        referral_migration(connection)
    return financial_postgres


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(referrals, "ENABLED", True)


def bounded(db):
    db.execute(text("SET LOCAL lock_timeout = '10s'"))
    db.execute(text("SET LOCAL statement_timeout = '20s'"))


def apply(factory, payer, referrer):
    with factory() as db:
        code = referrals.code_for(db, referrer).code
        referrals.apply(db, payer, code)
        db.commit()


def checkout(factory, payer, key):
    with factory() as db:
        row = reserve(db, user_id=payer, request_key=key, merchandise_paise=100000)
        order = dict(
            entity="order",
            id=f"order_ReferralRace{row.id}",
            receipt=row.receipt,
            currency="INR",
            amount=row.payable_paise,
            notes=dict(purpose="sona_rewards_checkout", reservation_id=str(row.id)),
            status="paid",
            amount_paid=row.payable_paise,
            amount_due=0,
        )
        snapshot = OrderSnapshot(
            user_id=payer,
            order_reference=order["id"],
            currency="INR",
            total=row.payable_paise / 100,
        )
        db.add(snapshot)
        db.flush()
        bind(db, reservation_id=row.id, user_id=payer, order_id=snapshot.id, provider_order=order)
        payment = dict(
            entity="payment",
            id=f"pay_ReferralRace{row.id}",
            order_id=order["id"],
            currency="INR",
            amount=row.payable_paise,
            status="captured",
            captured=True,
            amount_refunded=0,
            refund_status=None,
        )
        identity = row.id
        db.commit()
        return identity, order, payment


def post(factory, payer, terms):
    with factory() as db:
        bounded(db)
        identity, order, payment = terms
        settle(
            db,
            reservation_id=identity,
            user_id=payer,
            provider_order=order,
            provider_payment=payment,
        )
        db.commit()


def test_parallel_capture_retries_award_one_pair(referral_postgres):
    factory = referral_postgres
    payer, referrer = customers(factory, 2)
    apply(factory, payer, referrer)
    terms = checkout(factory, payer, "one")
    parallel(lambda _: post(factory, payer, terms))
    with factory() as db:
        assert (
            db.query(ReferralCredit).filter(ReferralCredit.user_id.in_([payer, referrer])).count()
            == 2
        )
        assert db.get(RewardAccount, payer).balance_points == 160
        assert db.get(RewardAccount, referrer).balance_points == 200


def test_parallel_distinct_first_orders_award_only_once(referral_postgres):
    factory = referral_postgres
    payer, referrer = customers(factory, 2)
    apply(factory, payer, referrer)
    terms = [checkout(factory, payer, "first"), checkout(factory, payer, "second")]
    parallel(lambda i: post(factory, payer, terms[i]), count=2)
    with factory() as db:
        assert db.query(ReferralApplication).filter_by(referred_id=payer).count() == 1
        assert (
            db.query(ReferralCredit).filter(ReferralCredit.user_id.in_([payer, referrer])).count()
            == 2
        )
        assert db.get(RewardAccount, payer).balance_points == 170
        assert db.get(RewardAccount, referrer).balance_points == 200


def test_reciprocal_referral_capture_has_no_account_lock_cycle(referral_postgres):
    factory = referral_postgres
    first, second = customers(factory, 2)
    apply(factory, first, second)
    apply(factory, second, first)
    work = [
        (first, checkout(factory, first, "first")),
        (second, checkout(factory, second, "second")),
    ]
    parallel(lambda i: post(factory, *work[i]), count=2)
    with factory() as db:
        assert (
            db.query(ReferralCredit).filter(ReferralCredit.user_id.in_([first, second])).count()
            == 4
        )
        assert [db.get(RewardAccount, i).balance_points for i in (first, second)] == [260, 260]


def test_parallel_application_cannot_bind_two_referrers(referral_postgres):
    factory = referral_postgres
    payer, first, second = customers(factory, 3)
    with factory() as db:
        codes = [referrals.code_for(db, i).code for i in (first, second)]
        db.commit()

    def worker(index):
        with factory() as db:
            bounded(db)
            try:
                row = referrals.apply(db, payer, codes[index])
                identity = row.id
                db.commit()
                return identity
            except HTTPException as error:
                db.rollback()
                assert error.status_code == 409
                return None

    results = parallel(worker, count=2)
    assert sum(x is not None for x in results) == 1
    with factory() as db:
        assert db.query(ReferralApplication).filter_by(referred_id=payer).count() == 1


def test_parallel_adverse_retries_hold_both_once_without_adjustment(referral_postgres):
    factory = referral_postgres
    payer, referrer = customers(factory, 2)
    apply(factory, payer, referrer)
    terms = checkout(factory, payer, "capture")
    post(factory, payer, terms)

    def worker(_):
        from rewards_vouchers_reservation_models import FinancialReservation

        with factory() as db:
            bounded(db)
            row = db.get(FinancialReservation, terms[0])
            hold_targets(
                db, row, [("dispute", f"synthetic-race-{row.id}", "Synthetic provider dispute")]
            )
            db.commit()

    parallel(worker)
    with factory() as db:
        assert (
            db.query(RewardVoucherHold)
            .filter(RewardVoucherHold.reward_user_id.in_([payer, referrer]))
            .count()
            == 2
        )
        assert [db.get(RewardAccount, i).balance_points for i in (payer, referrer)] == [160, 200]


@pytest.mark.parametrize(
    "table", ["referral_codes", "referral_applications", "referral_outcomes", "referral_credits"]
)
@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
def test_real_postgres_referral_history_is_immutable(referral_postgres, table, action):
    from sqlalchemy.exc import DatabaseError

    factory = referral_postgres
    payer, referrer = customers(factory, 2)
    apply(factory, payer, referrer)
    terms = checkout(factory, payer, "immutable")
    post(factory, payer, terms)
    with factory() as db:
        before = db.query(ReferralCredit).count()
        query = (
            f"DELETE FROM {table}"
            if action == "DELETE"
            else f"UPDATE {table} SET created_at=created_at"
        )
        with pytest.raises(DatabaseError):
            db.execute(text(query))
        db.rollback()
        assert db.query(ReferralCredit).count() == before
