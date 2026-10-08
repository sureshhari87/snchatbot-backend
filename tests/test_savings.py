from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from savings import (
    SavingsConflict,
    balance,
    bonus_basis_points,
    confirm_manual,
    enroll,
    gold_credit,
    maturity_at,
    payment_request,
    redeem,
    verify_razorpay,
)
from savings_models import SavingsAudit, SavingsEntry, SavingsGoldRate, SavingsPayment

START = datetime(2024, 1, 31, 12, 30)


def plan(db, user, kind="monthly", key="enroll"):
    return enroll(db, user.id, kind, key, START, 50000 if kind == "monthly" else None)


def rate(db, admin, amount=700000, effective=START, until=None):
    row = SavingsGoldRate(
        paise_per_gram=amount,
        source="Synthetic store rate",
        effective_at=effective,
        valid_until=until or START + timedelta(days=1000),
        created_by=admin.id,
    )
    db.add(row)
    db.flush()
    return row


def request(db, user, scheme, key="payment", mode="manual", now=START, amount=None):
    return payment_request(db, user.id, scheme.id, key, mode, now, amount)


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("monthly", datetime(2024, 12, 31, 12, 30)),
        ("digigold", START + timedelta(days=330)),
    ],
)
def test_approved_maturity(kind, expected):
    assert maturity_at(kind, START) == expected


def test_calendar_month_end_and_timezone():
    assert maturity_at("monthly", datetime(2024, 3, 31)) == datetime(2025, 2, 28)
    ist = timezone(timedelta(hours=5, minutes=30))
    assert maturity_at("monthly", START.replace(tzinfo=ist)) == datetime(2024, 12, 31, 7)


@pytest.mark.parametrize(
    "days,bps",
    [
        (0, 500),
        (75, 500),
        (76, 375),
        (150, 375),
        (151, 200),
        (225, 200),
        (226, 75),
        (300, 75),
        (301, 0),
        (330, 0),
    ],
)
def test_bonus_boundaries(days, bps):
    assert bonus_basis_points(START, START + timedelta(days=days)) == bps


def test_negative_elapsed_time_rejected():
    with pytest.raises(ValueError):
        bonus_basis_points(START, START - timedelta(seconds=1))


def test_integer_quantity_rounds_down_separately():
    # 100 rupees / 7000 rupees per gram = 0.014285714... g.
    assert gold_credit(10000, 700000, 500) == (14285, 714)
    assert gold_credit(10000, 700000, 375) == (14285, 535)


@pytest.mark.parametrize("amount", [True, 0, -1, 100.0, "100", 2**63])
def test_noninteger_or_invalid_money_rejected(amount):
    with pytest.raises(ValueError):
        gold_credit(amount, 700000, 500)


def test_enrollment_idempotent_changed_inputs_rejected(db, verified_user):
    scheme = plan(db, verified_user)
    assert plan(db, verified_user).id == scheme.id
    assert db.query(SavingsAudit).count() == 1
    with pytest.raises(SavingsConflict):
        enroll(db, verified_user.id, "monthly", "enroll", START, 60000)


def test_minimum_amounts_and_authoritative_monthly_amount(db, verified_user):
    with pytest.raises(ValueError):
        enroll(db, verified_user.id, "monthly", "low", START, 49999)
    scheme = plan(db, verified_user)
    with pytest.raises(ValueError):
        request(db, verified_user, scheme, amount=60000)
    digi = plan(db, verified_user, "digigold", "digi")
    with pytest.raises(ValueError):
        request(db, verified_user, digi, amount=9999)


def test_owner_and_payment_key_isolation(db, verified_user, admin_user):
    scheme = plan(db, verified_user)
    with pytest.raises(SavingsConflict):
        request(db, admin_user, scheme)
    payment = request(db, verified_user, scheme)
    assert request(db, verified_user, scheme).id == payment.id
    with pytest.raises(SavingsConflict):
        request(db, verified_user, scheme, mode="razorpay")
    with pytest.raises(SavingsConflict):
        request(db, verified_user, scheme, key="different")


def test_manual_confirmation_once_and_audit(db, verified_user, admin_user):
    scheme = plan(db, verified_user)
    payment = request(db, verified_user, scheme)
    with pytest.raises(PermissionError):
        confirm_manual(db, payment.id, verified_user, "receipt-1", START)
    first = confirm_manual(db, payment.id, admin_user, "receipt-1", START)
    assert confirm_manual(db, payment.id, admin_user, "receipt-1", START).id == first.id
    assert balance(db, scheme.id) == (50000, 0, 0)
    assert db.query(SavingsEntry).count() == 1
    assert db.query(SavingsAudit).filter_by(action="payment_posted").count() == 1
    with pytest.raises(SavingsConflict):
        confirm_manual(db, payment.id, admin_user, "different", START)


def test_receipt_cannot_credit_different_payment(db, verified_user, admin_user):
    first, second = plan(db, verified_user), plan(db, verified_user, key="second")
    p1 = request(db, verified_user, first)
    p2 = request(db, verified_user, second, key="second-payment")
    confirm_manual(db, p1.id, admin_user, "shared-receipt", START)
    with pytest.raises(SavingsConflict):
        confirm_manual(db, p2.id, admin_user, "shared-receipt", START)
    assert balance(db, second.id) == (0, 0, 0)


def test_rate_is_confirmation_time_not_request_time(db, verified_user, admin_user):
    scheme = plan(db, verified_user, "digigold")
    old = rate(db, admin_user)
    payment = request(db, verified_user, scheme, amount=10000)
    new = rate(db, admin_user, amount=800000, effective=START + timedelta(days=76))
    entry = confirm_manual(db, payment.id, admin_user, "digi-receipt", START + timedelta(days=76))
    assert entry.rate_id == new.id != old.id
    assert entry.gold_micrograms == 12500
    assert entry.bonus_micrograms == 468
    assert entry.bonus_bps == 375


def test_missing_expired_and_wrong_purity_rate_block_posting(db, verified_user, admin_user):
    scheme = plan(db, verified_user, "digigold")
    payment = request(db, verified_user, scheme, amount=10000)
    with pytest.raises(SavingsConflict):
        confirm_manual(db, payment.id, admin_user, "receipt", START)
    row = rate(db, admin_user, until=START + timedelta(seconds=1))
    with pytest.raises(SavingsConflict):
        confirm_manual(db, payment.id, admin_user, "receipt", START + timedelta(seconds=1))
    row.valid_until = START + timedelta(days=10)
    row.purity = "22K"
    db.flush()
    with pytest.raises(SavingsConflict):
        confirm_manual(db, payment.id, admin_user, "receipt", START)
    assert payment.state == "pending"
    assert db.query(SavingsEntry).count() == 0


def test_monthly_requires_time_and_eleven_confirmations(db, verified_user, admin_user):
    scheme = plan(db, verified_user)
    for index in range(11):
        payment = request(db, verified_user, scheme, key=f"installment-{index}")
        confirm_manual(db, payment.id, admin_user, f"receipt-{index}", START)
    with pytest.raises(SavingsConflict):
        request(db, verified_user, scheme, key="twelfth")
    with pytest.raises(SavingsConflict):
        redeem(
            db, scheme.id, admin_user, "redemption", scheme.matures_at - timedelta(microseconds=1)
        )
    entry = redeem(db, scheme.id, admin_user, "redemption", scheme.matures_at)
    assert entry.principal_paise == -550000
    assert entry.store_bonus_paise == 50000
    assert balance(db, scheme.id) == (0, 0, 0)
    assert redeem(db, scheme.id, admin_user, "redemption", scheme.matures_at).id == entry.id
    with pytest.raises(SavingsConflict):
        redeem(db, scheme.id, admin_user, "different", scheme.matures_at)
    with pytest.raises(SavingsConflict):
        request(db, verified_user, scheme, key="after-redemption")
    assert db.query(SavingsAudit).filter_by(action="redeemed").count() == 1


def test_elapsed_months_alone_do_not_allow_redemption(db, verified_user, admin_user):
    scheme = plan(db, verified_user)
    payment = request(db, verified_user, scheme)
    confirm_manual(db, payment.id, admin_user, "receipt", START)
    with pytest.raises(SavingsConflict):
        redeem(db, scheme.id, admin_user, "redemption", scheme.matures_at)


def test_digigold_maturity_pending_payments_and_empty_balance(db, verified_user, admin_user):
    scheme = plan(db, verified_user, "digigold")
    with pytest.raises(SavingsConflict):
        redeem(db, scheme.id, admin_user, "redemption", scheme.matures_at)
    rate(db, admin_user)
    payment = request(db, verified_user, scheme, amount=10000)
    with pytest.raises(SavingsConflict):
        redeem(db, scheme.id, admin_user, "redemption", scheme.matures_at)
    confirm_manual(db, payment.id, admin_user, "receipt", START)
    with pytest.raises(SavingsConflict):
        redeem(db, scheme.id, admin_user, "redemption", scheme.matures_at - timedelta(seconds=1))
    with pytest.raises(PermissionError):
        redeem(db, scheme.id, verified_user, "redemption", scheme.matures_at)
    entry = redeem(db, scheme.id, admin_user, "redemption", scheme.matures_at)
    assert (entry.gold_micrograms, entry.bonus_micrograms) == (-14285, -714)
    assert balance(db, scheme.id) == (0, 0, 0)


def checkout(db, user):
    scheme = plan(db, user)
    payment = request(db, user, scheme, mode="razorpay")
    payment.provider_order_id = "order_synthetic"
    db.flush()
    return scheme, payment


def evidence(**changes):
    result = dict(
        id="pay_synthetic",
        order_id="order_synthetic",
        amount=50000,
        currency="INR",
        status="captured",
        amount_refunded=0,
        refunded=False,
    )
    result.update(changes)
    return result


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": 50001},
        {"amount": True},
        {"amount": 50000.0},
        {"currency": "USD"},
        {"status": "authorized"},
        {"id": "other"},
        {"order_id": "other"},
        {"refunded": True},
        {"amount_refunded": 1},
    ],
)
def test_razorpay_requires_exact_captured_evidence(db, verified_user, changes):
    scheme, payment = checkout(db, verified_user)
    with pytest.raises(SavingsConflict):
        verify_razorpay(
            db,
            payment.id,
            verified_user.id,
            "pay_synthetic",
            lambda _: (200, evidence(**changes)),
            START,
        )
    assert balance(db, scheme.id) == (0, 0, 0)


def test_razorpay_posting_retry_and_ownership(db, verified_user, admin_user):
    scheme, payment = checkout(db, verified_user)
    with pytest.raises(SavingsConflict):
        verify_razorpay(
            db, payment.id, admin_user.id, "pay_synthetic", lambda _: (200, evidence()), START
        )
    entry = verify_razorpay(
        db, payment.id, verified_user.id, "pay_synthetic", lambda _: (200, evidence()), START
    )
    assert (
        verify_razorpay(
            db, payment.id, verified_user.id, "pay_synthetic", lambda _: (200, evidence()), START
        ).id
        == entry.id
    )
    assert balance(db, scheme.id) == (50000, 0, 0)
    assert entry.actor_id is None
    assert entry.evidence_reference == "pay_synthetic"


def test_provider_unavailable_is_not_payment_confirmation(db, verified_user):
    scheme, payment = checkout(db, verified_user)
    with pytest.raises(SavingsConflict):
        verify_razorpay(
            db, payment.id, verified_user.id, "pay_synthetic", lambda _: (None, evidence()), START
        )
    assert balance(db, scheme.id) == (0, 0, 0)


def test_outer_transaction_rollback_is_atomic(db, verified_user, admin_user):
    scheme = plan(db, verified_user)
    payment = request(db, verified_user, scheme)
    db.commit()
    confirm_manual(db, payment.id, admin_user, "receipt", START)
    db.rollback()
    assert db.get(SavingsPayment, payment.id).state == "pending"
    assert balance(db, scheme.id) == (0, 0, 0)
    assert db.query(SavingsAudit).filter_by(action="payment_posted").count() == 0


def test_database_uniqueness_is_last_line_of_defense(db, verified_user, admin_user):
    scheme = plan(db, verified_user)
    payment = request(db, verified_user, scheme)
    confirm_manual(db, payment.id, admin_user, "receipt", START)
    db.commit()
    db.add(
        SavingsEntry(
            scheme_id=scheme.id,
            event_key="different-key",
            payment_id=payment.id,
            kind="credit",
            principal_paise=50000,
            gold_micrograms=0,
            bonus_micrograms=0,
            store_bonus_paise=0,
            bonus_bps=0,
            evidence_reference="receipt",
            created_at=START,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    assert balance(db, scheme.id) == (50000, 0, 0)


def test_monthly_calendar_uses_india_month_end():
    # Jan 31 at 00:30 IST is Jan 30 at 19:00 UTC.
    assert maturity_at("monthly", datetime(2024, 1, 30, 19)) == datetime(2024, 12, 30, 19)
    # Mar 31 at 00:30 IST matures Feb 28 at 00:30 IST, not Feb 27.
    assert maturity_at("monthly", datetime(2024, 3, 30, 19)) == datetime(2025, 2, 27, 19)


@pytest.mark.parametrize("kind", ["monthly", "digigold"])
def test_cumulative_accounting_overflow_does_not_post(db, verified_user, admin_user, kind):
    amount = 2**62 if kind == "monthly" else 2**61
    scheme = enroll(
        db,
        verified_user.id,
        kind,
        "large",
        START,
        amount if kind == "monthly" else None,
    )
    if kind == "digigold":
        rate(db, admin_user, amount=500000)
    first = request(db, verified_user, scheme, key="first", amount=amount)
    credited = confirm_manual(db, first.id, admin_user, "large-first", START)
    before = balance(db, scheme.id)
    second = request(db, verified_user, scheme, key="second", amount=amount)
    with pytest.raises(ValueError, match="accounting range"):
        confirm_manual(db, second.id, admin_user, "large-second", START)
    assert second.state == "pending"
    assert balance(db, scheme.id) == before
    assert db.query(SavingsEntry).count() == 1
    assert db.query(SavingsAudit).filter_by(action="payment_posted").count() == 1
    assert confirm_manual(db, first.id, admin_user, "large-first", START).id == credited.id
