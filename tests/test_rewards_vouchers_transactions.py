"""Isolated transaction checks; no provider or external database access."""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import OrderSnapshot, User, utc_now
from rewards_vouchers_models import RewardAccount, RewardEntry
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent
from rewards_vouchers_transactions import bind, reserve, settle


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    from test_rewards_vouchers_reservation_migration import migrations

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(connection, tables=[User.__table__, OrderSnapshot.__table__])
        migrations(connection)
    with Session(engine) as session:
        session.add_all(
            [
                User(
                    id=i,
                    username=f"test{i}",
                    email=f"test{i}@example.invalid",
                    hashed_password="not-a-password",
                )
                for i in (1, 2)
            ]
        )
        session.add(
            RewardAccount(user_id=1, balance_points=100, reserved_points=0, created_at=utc_now())
        )
        session.commit()
        yield session
    engine.dispose()


def reservation(db, **changes):
    args = dict(
        user_id=1, request_key="checkout-one", merchandise_paise=30000, reward_points_requested=50
    )
    args.update(changes)
    return reserve(db, **args)


def ready(db, **changes):
    row = reservation(db, **changes)
    order = dict(
        entity="order",
        id="order_Test",
        receipt=row.receipt,
        currency="INR",
        amount=row.payable_paise,
        notes=dict(purpose="sona_rewards_checkout", reservation_id=str(row.id)),
        status="paid",
        amount_paid=row.payable_paise,
        amount_due=0,
    )
    local = OrderSnapshot(
        user_id=1, order_reference=order["id"], currency="INR", total=row.payable_paise / 100
    )
    db.add(local)
    db.flush()
    bind(db, reservation_id=row.id, user_id=1, order_id=local.id, provider_order=order)
    payment = dict(
        entity="payment",
        id="pay_Test",
        order_id=order["id"],
        currency="INR",
        amount=row.payable_paise,
        status="captured",
        captured=True,
        amount_refunded=0,
        refund_status=None,
    )
    return row, order, payment


def test_reservation_retry_never_reserves_twice(db):
    first = reservation(db)
    assert reservation(db).id == first.id
    assert db.get(RewardAccount, 1).reserved_points == 50
    assert db.query(FinancialReservationEvent).count() == 1
    with pytest.raises(HTTPException):
        reservation(db, merchandise_paise=40000)


def test_keys_are_customer_scoped(db):
    first = reservation(db)
    second = reservation(db, user_id=2, reward_points_requested=0)
    assert first.id != second.id


def test_later_reservation_can_only_spend_unreserved_points(db):
    reservation(db)
    second = reservation(db, request_key="two", reward_points_requested=100)
    assert second.reward_points == 50
    third = reservation(db, request_key="three", reward_points_requested=100)
    assert third.reward_points == 0
    assert db.get(RewardAccount, 1).reserved_points == 100


def test_capture_retry_posts_one_debit_and_one_credit(db):
    row, order, payment = ready(db)
    for _ in range(2):
        settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    assert db.query(RewardEntry).count() == 2
    assert db.get(RewardAccount, 1).balance_points == 52
    assert db.get(RewardAccount, 1).reserved_points == 0
    assert db.query(FinancialReservationEvent).count() == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "USD"),
        ("amount", True),
        ("amount", 1),
        ("status", "authorized"),
        ("captured", False),
        ("amount_refunded", 1),
        ("order_id", "order_Other"),
    ],
)
def test_unverified_payment_does_not_post(db, field, value):
    row, order, payment = ready(db)
    payment[field] = value
    with pytest.raises(HTTPException):
        settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    assert db.query(RewardEntry).count() == 0
    assert db.get(RewardAccount, 1).reserved_points == 50


@pytest.mark.parametrize(
    "field,value",
    [
        ("receipt", "other"),
        ("amount", 1),
        ("currency", "USD"),
        ("status", "created"),
        ("amount_due", 100),
    ],
)
def test_wrong_provider_order_does_not_post(db, field, value):
    row, order, payment = ready(db)
    order[field] = value
    with pytest.raises(HTTPException):
        settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    assert db.query(RewardEntry).count() == 0


def test_wrong_customer_cannot_post(db):
    row, order, payment = ready(db)
    with pytest.raises(HTTPException) as error:
        settle(db, reservation_id=row.id, user_id=2, provider_order=order, provider_payment=payment)
    assert error.value.status_code == 404


def test_downstream_failure_rolls_back_posting(db):
    row, order, payment = ready(db)
    db.commit()
    row_id = row.id
    settle(db, reservation_id=row_id, user_id=1, provider_order=order, provider_payment=payment)
    db.rollback()
    assert db.query(RewardEntry).count() == 0
    assert db.get(RewardAccount, 1).balance_points == 100
    assert db.get(RewardAccount, 1).reserved_points == 50
    assert db.get(FinancialReservation, row_id).state == "ready"


def voucher(db, **changes):
    import hashlib
    from datetime import timedelta

    from rewards_vouchers_models import GiftVoucher

    now = utc_now()
    values = dict(
        purchaser_id=1,
        request_key="voucher",
        code_hash=hashlib.sha256(b"TEST-CODE").hexdigest(),
        source="purchased",
        amount_paise=20000,
        balance_paise=20000,
        reserved_paise=0,
        state="active",
        activated_at=now,
        expires_at=now + timedelta(days=365),
        created_at=now,
    )
    if "expires_at" in changes:
        values["activated_at"] = changes["expires_at"] - timedelta(days=365)
    values.update(changes)
    item = GiftVoucher(**values)
    db.add(item)
    db.flush()
    return item


def test_voucher_is_applied_before_rewards_and_partially_spent(db):
    item = voucher(db)
    row = reservation(db, voucher_code="test-code")
    assert row.voucher_paise == 20000
    assert row.reward_points == 50
    assert row.payable_paise == 5000
    assert item.reserved_paise == 20000
    second = reservation(db, request_key="second", voucher_code="TEST-CODE")
    assert second.voucher_paise == 0


@pytest.mark.parametrize("changes", [{"assigned_user_id": 2}, {"expires_at": utc_now()}])
def test_expired_or_other_customer_voucher_is_unavailable(db, changes):
    voucher(db, **changes)
    with pytest.raises(HTTPException) as error:
        reservation(db, voucher_code="TEST-CODE")
    assert error.value.status_code == 404
    assert db.query(FinancialReservation).count() == 0


def test_voucher_preserves_one_rupee_payment(db):
    item = voucher(db)
    row = reservation(db, voucher_code="TEST-CODE", merchandise_paise=10000)
    assert row.voucher_paise == 9900
    assert row.reward_points == 0
    assert row.payable_paise == 100
    assert item.reserved_paise == 9900


def test_unknown_payment_result_keeps_reservation(db):
    row, order, payment = ready(db)
    payment["status"] = "authorized"
    with pytest.raises(HTTPException):
        settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    assert row.state == "ready"
    assert db.get(RewardAccount, 1).reserved_points == 50


def test_held_reward_account_blocks_new_reservations(db):
    from rewards_vouchers_models import RewardVoucherHold

    db.add(
        RewardVoucherHold(
            reward_user_id=1,
            kind="dispute",
            reference="synthetic-dispute",
            reason="Synthetic adverse evidence",
            created_at=utc_now(),
        )
    )
    db.flush()
    with pytest.raises(HTTPException):
        reservation(db)
    assert db.query(FinancialReservation).count() == 0


def test_hold_after_checkout_blocks_posting_without_releasing_balance(db):
    from rewards_vouchers_models import RewardVoucherHold

    row, order, payment = ready(db)
    db.add(
        RewardVoucherHold(
            reward_user_id=1,
            kind="refund",
            reference="synthetic-refund",
            reason="Synthetic adverse evidence",
            created_at=utc_now(),
        )
    )
    db.flush()
    with pytest.raises(HTTPException):
        settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    assert db.query(RewardEntry).count() == 0
    assert db.get(RewardAccount, 1).reserved_points == 50
    assert row.state == "ready"


def test_duplicate_hold_is_a_single_immutable_audit(db):
    from rewards_vouchers_models import RewardVoucherHold
    from rewards_vouchers_transactions import place_hold

    terms = dict(
        kind="dispute",
        reference="synthetic-dispute",
        reason="Synthetic provider evidence",
        reward_user_id=1,
    )
    first = place_hold(db, **terms)
    assert place_hold(db, **terms).id == first.id
    assert db.query(RewardVoucherHold).count() == 1
    assert db.get(RewardAccount, 1).balance_points == 100
    assert db.get(RewardAccount, 1).reserved_points == 0
    with pytest.raises(HTTPException):
        place_hold(db, **(terms | {"reason": "changed audit"}))


def test_admin_review_rejects_customer_and_requires_actor(db):
    from rewards_vouchers_transactions import place_hold

    for actor_id in (None, 1):
        with pytest.raises(HTTPException) as error:
            place_hold(
                db,
                kind="admin_review",
                reference="synthetic",
                reason="Review",
                reward_user_id=1,
                actor_id=actor_id,
            )
        assert error.value.status_code == 403


def test_held_voucher_cannot_be_reserved(db):
    from rewards_vouchers_transactions import place_hold

    item = voucher(db)
    place_hold(
        db,
        kind="refund",
        reference="synthetic-refund",
        reason="Synthetic adverse funding",
        voucher_id=item.id,
    )
    with pytest.raises(HTTPException):
        reservation(db, voucher_code="TEST-CODE")
    assert item.reserved_paise == 0


def test_hold_after_voucher_checkout_keeps_reserved_value(db):
    from rewards_vouchers_models import GiftVoucherEntry
    from rewards_vouchers_transactions import place_hold

    item = voucher(db)
    row, order, payment = ready(db, voucher_code="TEST-CODE")
    place_hold(
        db,
        kind="dispute",
        reference="synthetic-dispute",
        reason="Synthetic adverse funding",
        voucher_id=item.id,
    )
    with pytest.raises(HTTPException):
        settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    assert item.balance_paise == 20000
    assert item.reserved_paise == 20000
    assert db.query(GiftVoucherEntry).count() == 0
    assert row.state == "ready"


def test_expiry_after_reservation_blocks_late_posting_and_keeps_value(db, monkeypatch):
    from datetime import timedelta

    import rewards_vouchers_transactions
    from rewards_vouchers_models import GiftVoucherEntry

    item = voucher(db)
    row, order, payment = ready(db, voucher_code="TEST-CODE")
    monkeypatch.setattr(
        rewards_vouchers_transactions, "utc_now", lambda: item.expires_at + timedelta(seconds=1)
    )
    with pytest.raises(HTTPException):
        settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    assert item.balance_paise == 20000 and item.reserved_paise == 20000
    assert row.state == "ready"
    assert db.query(GiftVoucherEntry).count() == 0
