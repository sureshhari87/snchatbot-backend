"""Opt-in races on disposable loopback PostgreSQL; never uses DATABASE_URL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from test_payment_concurrency_postgres import local_postgres as _pg
from test_rewards_vouchers_reservation_migration import migrations

from models import OrderSnapshot, User, utc_now
from rewards_vouchers_models import (
    GiftVoucher,
    GiftVoucherEntry,
    RewardAccount,
    RewardEntry,
    RewardVoucherHold,
)
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent
from rewards_vouchers_transactions import bind, reserve, settle

local_postgres = _pg


@pytest.fixture(scope="module")
def financial_postgres(local_postgres):
    engine = local_postgres.kw["bind"]
    tables = [
        FinancialReservationEvent.__table__,
        FinancialReservation.__table__,
        RewardVoucherHold.__table__,
        GiftVoucherEntry.__table__,
        RewardEntry.__table__,
        GiftVoucher.__table__,
        RewardAccount.__table__,
    ]
    with engine.begin() as connection:
        for table in tables:
            table.drop(connection)
        migrations(connection)
    return local_postgres


def customers(factory, count=1):
    with factory() as db:
        ids = []
        for _ in range(count):
            label = "reward_race_" + uuid4().hex
            user = User(username=label, email=label + "@example.invalid", hashed_password="unused")
            db.add(user)
            db.flush()
            db.add(
                RewardAccount(
                    user_id=user.id, balance_points=100, reserved_points=0, created_at=utc_now()
                )
            )
            ids.append(user.id)
        db.commit()
        return ids


def parallel(action, count=4):
    barrier = Barrier(count)

    def worker(index):
        barrier.wait(timeout=20)
        return action(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(worker, range(count)))


@pytest.mark.parametrize("same_key", [True, False])
def test_parallel_reward_reservations_never_double_spend(financial_postgres, same_key):
    factory = financial_postgres
    user_id = customers(factory)[0]

    def action(index):
        with factory() as db:
            row = reserve(
                db,
                user_id=user_id,
                request_key="same" if same_key else f"key{index}",
                merchandise_paise=30000,
                reward_points_requested=100,
            )
            row_id = row.id
            used = row.reward_points
            db.commit()
            return row_id, used

    result = parallel(action)
    with factory() as db:
        rows = db.query(FinancialReservation).filter_by(user_id=user_id).all()
        assert len(rows) == (1 if same_key else 4)
        assert sum(row.reward_points for row in rows) == 100
        assert db.get(RewardAccount, user_id).reserved_points == 100
        if same_key:
            assert len({row_id for row_id, _ in result}) == 1


def test_two_customers_cannot_double_spend_bearer_voucher(financial_postgres):
    import hashlib

    factory = financial_postgres
    ids = customers(factory, 2)
    code = "TEST-" + uuid4().hex.upper()
    with factory() as db:
        now = utc_now()
        voucher = GiftVoucher(
            purchaser_id=ids[0],
            request_key=uuid4().hex,
            code_hash=hashlib.sha256(code.encode()).hexdigest(),
            source="purchased",
            amount_paise=20000,
            balance_paise=20000,
            reserved_paise=0,
            state="active",
            activated_at=now,
            expires_at=now + timedelta(days=365),
            created_at=now,
        )
        db.add(voucher)
        db.commit()
        voucher_id = voucher.id

    def action(index):
        with factory() as db:
            row = reserve(
                db,
                user_id=ids[index],
                request_key="same",
                merchandise_paise=15000,
                voucher_code=code,
            )
            used = row.voucher_paise
            db.commit()
            return used

    amounts = parallel(action, 2)
    assert sorted(amounts) == [5100, 14900]
    with factory() as db:
        assert db.get(GiftVoucher, voucher_id).reserved_paise == 20000


def paid(factory, user_id):
    with factory() as db:
        row = reserve(
            db,
            user_id=user_id,
            request_key="capture",
            merchandise_paise=30000,
            reward_points_requested=50,
        )
        label = uuid4().hex
        order = dict(
            entity="order",
            id="order_" + label,
            receipt=row.receipt,
            currency="INR",
            amount=row.payable_paise,
            notes=dict(purpose="sona_rewards_checkout", reservation_id=str(row.id)),
            status="paid",
            amount_paid=row.payable_paise,
            amount_due=0,
        )
        local = OrderSnapshot(
            user_id=user_id,
            order_reference=order["id"],
            currency="INR",
            total=row.payable_paise / 100,
        )
        db.add(local)
        db.flush()
        bind(db, reservation_id=row.id, user_id=user_id, order_id=local.id, provider_order=order)
        payment = dict(
            entity="payment",
            id="pay_" + label,
            order_id=order["id"],
            currency="INR",
            amount=row.payable_paise,
            status="captured",
            captured=True,
            amount_refunded=0,
            refund_status=None,
        )
        db.commit()
        return row.id, order, payment


def test_parallel_callbacks_post_once(financial_postgres):
    factory = financial_postgres
    user_id = customers(factory)[0]
    row_id, order, payment = paid(factory, user_id)

    def action(_):
        with factory() as db:
            settle(
                db,
                reservation_id=row_id,
                user_id=user_id,
                provider_order=order,
                provider_payment=payment,
            )
            db.commit()

    parallel(action)
    with factory() as db:
        assert db.query(RewardEntry).filter_by(user_id=user_id).count() == 2
        assert db.query(FinancialReservationEvent).filter_by(reservation_id=row_id).count() == 3
        assert db.get(RewardAccount, user_id).balance_points == 52
        assert db.get(RewardAccount, user_id).reserved_points == 0


def test_postgres_rolls_back_entire_failed_posting(financial_postgres):
    factory = financial_postgres
    user_id = customers(factory)[0]
    row_id, order, payment = paid(factory, user_id)
    with factory() as db:
        with pytest.raises(RuntimeError):
            with db.begin():
                settle(
                    db,
                    reservation_id=row_id,
                    user_id=user_id,
                    provider_order=order,
                    provider_payment=payment,
                )
                raise RuntimeError("Synthetic downstream inventory failure")
    with factory() as db:
        assert db.query(RewardEntry).filter_by(user_id=user_id).count() == 0
        assert db.get(RewardAccount, user_id).balance_points == 100
        assert db.get(RewardAccount, user_id).reserved_points == 50
        assert db.get(FinancialReservation, row_id).state == "ready"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE financial_reservations SET quote_json='{}' WHERE id=:id",
        "DELETE FROM financial_reservations WHERE id=:id",
        "UPDATE financial_reservations SET provider_order_id='order_Other' WHERE id=:id",
        "DELETE FROM financial_reservation_events WHERE reservation_id=:id",
    ],
)
def test_postgres_migration_guards_preserve_financial_evidence(financial_postgres, sql):
    factory = financial_postgres
    user_id = customers(factory)[0]
    row_id, _, _ = paid(factory, user_id)
    with factory() as db:
        with pytest.raises(DatabaseError):
            db.execute(text(sql), dict(id=row_id))
        db.rollback()
        assert db.get(FinancialReservation, row_id).state == "ready"
        assert db.query(FinancialReservationEvent).filter_by(reservation_id=row_id).count() == 2


def test_parallel_adverse_events_create_one_hold_without_balance_changes(financial_postgres):
    from rewards_vouchers_transactions import place_hold

    factory = financial_postgres
    user_id = customers(factory)[0]

    def action(_):
        with factory() as db:
            row = place_hold(
                db,
                kind="dispute",
                reference="same-evidence",
                reason="Synthetic provider evidence",
                reward_user_id=user_id,
            )
            row_id = row.id
            db.commit()
            return row_id

    ids = parallel(action)
    assert len(set(ids)) == 1
    with factory() as db:
        assert db.query(RewardVoucherHold).filter_by(reward_user_id=user_id).count() == 1
        assert db.get(RewardAccount, user_id).balance_points == 100
        assert db.get(RewardAccount, user_id).reserved_points == 0


def test_concurrent_hold_and_reservation_preserve_money(financial_postgres):
    from fastapi import HTTPException

    from rewards_vouchers_transactions import place_hold

    factory = financial_postgres
    user_id = customers(factory)[0]

    def action(index):
        with factory() as db:
            if index == 0:
                place_hold(
                    db,
                    kind="refund",
                    reference="adverse",
                    reason="Synthetic provider evidence",
                    reward_user_id=user_id,
                )
                db.commit()
                return "held"
            try:
                reserve(
                    db,
                    user_id=user_id,
                    request_key="racing",
                    merchandise_paise=30000,
                    reward_points_requested=100,
                )
                db.commit()
                return "reserved"
            except HTTPException as error:
                assert error.status_code == 409
                db.rollback()
                return "blocked"

    results = parallel(action, 2)
    assert "held" in results
    with factory() as db:
        account = db.get(RewardAccount, user_id)
        rows = db.query(FinancialReservation).filter_by(user_id=user_id).all()
        assert account.balance_points == 100
        assert account.reserved_points == sum(row.reward_points for row in rows)
        assert db.query(RewardVoucherHold).filter_by(reward_user_id=user_id).count() == 1
        assert db.query(RewardEntry).filter_by(user_id=user_id).count() == 0


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE gift_vouchers SET amount_paise=30000 WHERE id=:id",
        "UPDATE gift_vouchers SET code_hash='other' WHERE id=:id",
        "UPDATE gift_vouchers SET assigned_user_id=purchaser_id WHERE id=:id",
        "UPDATE gift_vouchers SET expires_at='2030-01-01' WHERE id=:id",
        "DELETE FROM gift_vouchers WHERE id=:id",
    ],
)
def test_postgres_voucher_terms_cannot_change(financial_postgres, sql):
    import hashlib

    factory = financial_postgres
    user_id = customers(factory)[0]
    with factory() as db:
        now = utc_now()
        voucher = GiftVoucher(
            purchaser_id=user_id,
            request_key=uuid4().hex,
            code_hash=hashlib.sha256(uuid4().hex.encode()).hexdigest(),
            source="purchased",
            amount_paise=20000,
            balance_paise=20000,
            reserved_paise=0,
            state="active",
            activated_at=now,
            expires_at=now + timedelta(days=365),
            created_at=now,
        )
        db.add(voucher)
        db.commit()
        row_id = voucher.id
        with pytest.raises(DatabaseError):
            db.execute(text(sql), dict(id=row_id))
        db.rollback()
        assert db.get(GiftVoucher, row_id).amount_paise == 20000
