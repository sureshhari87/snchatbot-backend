"""Approved referral behavior on synthetic, isolated migrated SQLite only."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session
from test_rewards_vouchers_reservation_migration import migrations

import referrals
from database import Base
from financial_checkout import hold_targets
from models import OrderSnapshot, User, utc_now
from referral_models import ReferralApplication, ReferralCode, ReferralCredit, ReferralOutcome
from rewards_vouchers_models import RewardAccount, RewardVoucherHold
from rewards_vouchers_transactions import place_hold, settle


def referral_migration(connection):
    spec = importlib.util.spec_from_file_location(
        "referral_revision",
        Path(__file__).resolve().parents[1] / "alembic/versions/0034_referrals.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    module.upgrade()
    return module


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(referrals, "ENABLED", True)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(connection, tables=[User.__table__, OrderSnapshot.__table__])
        migrations(connection)
        referral_migration(connection)
    with Session(engine) as session:
        session.add_all(
            [
                User(
                    id=i,
                    username=f"referral{i}",
                    email=f"referral{i}@example.invalid",
                    hashed_password="unused",
                    is_admin=i == 4,
                )
                for i in (1, 2, 3, 4)
            ]
        )
        session.add(
            RewardAccount(user_id=1, balance_points=100, reserved_points=0, created_at=utc_now())
        )
        session.commit()
        yield session
    engine.dispose()


def ready(db, **changes):
    from rewards_vouchers_transactions import bind, reserve

    terms = dict(
        user_id=1, request_key="checkout-one", merchandise_paise=100000, reward_points_requested=0
    )
    terms.update(changes)
    row = reserve(db, **terms)
    order = dict(
        entity="order",
        id=f"order_Referral{row.id}",
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
        id=f"pay_Referral{row.id}",
        order_id=order["id"],
        currency="INR",
        amount=row.payable_paise,
        status="captured",
        captured=True,
        amount_refunded=0,
        refund_status=None,
    )
    return row, order, payment


def application(db, referrer=2):
    code = referrals.code_for(db, referrer).code
    return referrals.apply(db, 1, code)


def capture(db, **changes):
    terms = dict(merchandise_paise=100000, reward_points_requested=0)
    terms.update(changes)
    row, order, payment = ready(db, **terms)
    settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    db.commit()
    return row, order, payment


def test_code_is_stable_and_does_not_embed_customer_identity(db):
    first = referrals.code_for(db, 2).code
    assert first.startswith("SONA") and len(first) == 16
    assert referrals.code_for(db, 2).code == first
    assert db.query(ReferralCode).count() == 1
    assert referrals.programme(db, 2)["pointsEarned"] == 0


def test_application_normalizes_and_retries_without_rebinding(db):
    code = referrals.code_for(db, 2).code
    first = referrals.apply(db, 1, "  " + code.lower() + "  ")
    assert referrals.apply(db, 1, code).id == first.id
    other = referrals.code_for(db, 3).code
    with pytest.raises(HTTPException):
        referrals.apply(db, 1, other)
    assert db.query(ReferralApplication).count() == 1


@pytest.mark.parametrize("value", [None, "bad code", "short", 123])
def test_invalid_codes_fail_closed(db, value):
    with pytest.raises(HTTPException):
        referrals.apply(db, 1, value)
    assert db.query(ReferralApplication).count() == 0


def test_self_and_admin_referrals_rejected(db):
    own = referrals.code_for(db, 1).code
    with pytest.raises(HTTPException):
        referrals.apply(db, 1, own)
    with pytest.raises(HTTPException):
        referrals.code_for(db, 4)
    with pytest.raises(HTTPException):
        referrals.apply(db, 4, own)


def test_prior_verified_order_blocks_application(db):
    code = referrals.code_for(db, 2).code
    db.add(OrderSnapshot(user_id=1, order_reference="synthetic-prior", payment_status="paid"))
    db.flush()
    with pytest.raises(HTTPException):
        referrals.apply(db, 1, code)


def test_approved_credit_pair_posts_once_and_survives_duplicate_capture(db):
    app = application(db)
    row, order, payment = capture(db)
    assert db.get(RewardAccount, 1).balance_points == 160
    assert db.get(RewardAccount, 2).balance_points == 100
    assert db.query(ReferralCredit).count() == 2
    assert db.query(ReferralOutcome).one().state == "rewarded"
    assert referrals.apply(db, 1, app.code).id == app.id
    settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    db.commit()
    assert db.query(ReferralCredit).count() == 2
    assert referrals.programme(db, 2)["pointsEarned"] == 100
    assert referrals.programme(db, 1)["applicationStatus"] == "rewarded"


@pytest.mark.parametrize(
    "terms",
    [
        dict(merchandise_paise=99999, tax_paise=10000, delivery_paise=10000),
        dict(merchandise_paise=110000, coupon_discount_paise=10001),
    ],
)
def test_threshold_excludes_tax_delivery_and_applies_coupon(db, terms):
    application(db)
    capture(db, **terms)
    assert db.query(ReferralOutcome).one().state == "not_qualified"
    assert db.query(ReferralCredit).count() == 0


def test_threshold_is_before_reward_redemption(db):
    application(db)
    capture(db, merchandise_paise=110000, coupon_discount_paise=10000, reward_points_requested=100)
    assert db.query(ReferralCredit).count() == 2
    assert db.get(RewardAccount, 1).balance_points == 59


def test_low_first_purchase_cannot_award_on_later_purchase(db):
    application(db)
    capture(db, merchandise_paise=90000)
    row, order, payment = ready(
        db, request_key="second", merchandise_paise=200000, reward_points_requested=0
    )
    settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    db.commit()
    assert db.query(ReferralOutcome).one().state == "not_qualified"
    assert db.query(ReferralCredit).count() == 0
    assert referrals.previous_purchase(db, 1)


def test_referrer_hold_defers_both_referral_credits_without_blocking_purchase(db):
    application(db)
    place_hold(
        db,
        kind="dispute",
        reference="synthetic-dispute",
        reason="synthetic evidence",
        reward_user_id=2,
    )
    row, _, _ = capture(db)
    assert row.state == "posted"
    assert db.query(ReferralOutcome).one().state == "held"
    assert db.query(ReferralCredit).count() == 0
    assert db.get(RewardAccount, 1).balance_points == 110


def test_adverse_evidence_holds_both_accounts_even_after_disable(db, monkeypatch):
    application(db)
    row, _, _ = capture(db)
    balances = [db.get(RewardAccount, i).balance_points for i in (1, 2)]
    monkeypatch.setattr(referrals, "ENABLED", False)
    adverse = [("refund", "synthetic-refund", "Synthetic verified refund")]
    hold_targets(db, row, adverse)
    hold_targets(db, row, adverse)
    db.commit()
    assert {x.reward_user_id for x in db.query(RewardVoucherHold)} == {1, 2}
    assert db.query(RewardVoucherHold).count() == 2
    assert [db.get(RewardAccount, i).balance_points for i in (1, 2)] == balances
    assert db.query(ReferralCredit).count() == 2


def test_default_off_never_creates_application_or_award(db, monkeypatch):
    monkeypatch.setattr(referrals, "ENABLED", False)
    with pytest.raises(HTTPException) as error:
        referrals.code_for(db, 1)
    assert error.value.status_code == 503
    assert referrals.prepare(db, 1) is None
    capture(db)
    assert db.query(ReferralOutcome).count() == 0


def test_failed_capture_cannot_create_referral_evidence(db):
    application(db)
    row, order, payment = ready(db, merchandise_paise=100000, reward_points_requested=0)
    payment["captured"] = False
    with pytest.raises(HTTPException):
        settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    assert db.query(ReferralOutcome).count() == 0
    assert db.query(ReferralCredit).count() == 0


@pytest.mark.parametrize(
    "table", ["referral_codes", "referral_applications", "referral_outcomes", "referral_credits"]
)
@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
def test_actual_migration_makes_evidence_immutable(db, table, action):
    application(db)
    capture(db)
    sql = (
        f"DELETE FROM {table}"
        if action == "DELETE"
        else f"UPDATE {table} SET created_at=created_at"
    )
    with pytest.raises(DatabaseError):
        db.execute(text(sql))
    db.rollback()
    assert db.query(ReferralCredit).count() == 2


def test_revision_preserves_existing_core_records_and_blocks_destructive_downgrade():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(connection, tables=[User.__table__, OrderSnapshot.__table__])
        migrations(connection)
        connection.execute(
            User.__table__.insert().values(
                id=1,
                username="preserved",
                email="preserved@example.invalid",
                hashed_password="unused",
                is_verified=True,
                is_admin=False,
            )
        )
        connection.execute(
            RewardAccount.__table__.insert().values(
                user_id=1, balance_points=75, reserved_points=0, created_at=utc_now()
            )
        )
        revision = referral_migration(connection)
        assert (
            connection.execute(
                text("SELECT balance_points FROM reward_accounts WHERE user_id=1")
            ).scalar_one()
            == 75
        )
        connection.execute(
            ReferralCode.__table__.insert().values(
                owner_id=1, code="SONASYNTHETIC123", created_at=utc_now()
            )
        )
        with pytest.raises(RuntimeError):
            revision.downgrade()
        assert connection.execute(text("SELECT count(*) FROM referral_codes")).scalar_one() == 1
    engine.dispose()


@pytest.mark.parametrize("target", [1, 2])
def test_referral_balance_overflow_rolls_back_entire_capture(db, target):
    application(db)
    referrer = db.get(RewardAccount, target)
    referrer.balance_points = referrals.MAX_POINTS
    row, order, payment = ready(db)
    identity = row.id
    db.commit()
    with pytest.raises(HTTPException):
        settle(
            db, reservation_id=identity, user_id=1, provider_order=order, provider_payment=payment
        )
    db.rollback()
    from rewards_vouchers_reservation_models import FinancialReservation

    assert db.get(FinancialReservation, identity).state == "ready"
    assert db.query(ReferralCredit).count() == 0
    assert db.query(ReferralOutcome).count() == 0
    assert db.get(RewardAccount, 1).balance_points == (referrals.MAX_POINTS if target == 1 else 100)


@pytest.mark.parametrize(
    "field,value",
    [
        ("terms_version", 2),
        ("referrer_points", 99),
        ("customer_points", 49),
        ("minimum_merchandise_paise", 99999),
    ],
)
def test_database_rejects_unapproved_terms(db, field, value):
    code = referrals.code_for(db, 2).code
    terms = dict(
        referred_id=1,
        referrer_id=2,
        code=code,
        terms_version=1,
        referrer_points=100,
        customer_points=50,
        minimum_merchandise_paise=100000,
        created_at=utc_now(),
    )
    terms[field] = value
    with pytest.raises(DatabaseError):
        db.execute(ReferralApplication.__table__.insert().values(**terms))
    db.rollback()


def test_database_binds_code_to_its_actual_owner(db):
    code = referrals.code_for(db, 2).code
    with pytest.raises(DatabaseError):
        db.execute(
            ReferralApplication.__table__.insert().values(
                referred_id=1,
                referrer_id=3,
                code=code,
                terms_version=1,
                referrer_points=100,
                customer_points=50,
                minimum_merchandise_paise=100000,
                created_at=utc_now(),
            )
        )
    db.rollback()


def test_programme_reports_held_credits_without_reversing_balances(db):
    application(db)
    row, _, _ = capture(db)
    hold_targets(db, row, [("refund", "synthetic-held-status", "Synthetic verified refund")])
    db.commit()
    assert referrals.programme(db, 1)["applicationStatus"] == "held"
    assert referrals.programme(db, 1)["reviewRequired"] is True
    assert referrals.programme(db, 2)["reviewRequired"] is True
    assert referrals.programme(db, 2)["pointsEarned"] == 100


@pytest.mark.parametrize("identity", [1, 2])
def test_role_change_defers_referral_award_for_review(db, identity):
    application(db)
    db.get(User, identity).is_admin = True
    db.flush()
    capture(db)
    assert db.query(ReferralOutcome).one().state == "held"
    assert db.query(ReferralCredit).count() == 0


def test_disabling_after_application_records_no_award_and_never_retrocredits(db, monkeypatch):
    application(db)
    monkeypatch.setattr(referrals, "ENABLED", False)
    row, order, payment = capture(db)
    outcome = db.query(ReferralOutcome).one()
    assert outcome.state == "not_qualified" and outcome.reason == "programme_disabled"
    assert db.query(ReferralCredit).count() == 0
    monkeypatch.setattr(referrals, "ENABLED", True)
    settle(db, reservation_id=row.id, user_id=1, provider_order=order, provider_payment=payment)
    db.commit()
    assert db.query(ReferralCredit).count() == 0
    assert referrals.programme(db, 1)["applicationStatus"] == "not_qualified"


def test_payment_response_preserves_referrer_award_meaning_and_is_idempotent(db):
    from financial_checkout import post

    application(db)
    row, order, payment = ready(db)
    result = post(db, row, order, payment)
    db.commit()
    assert result["referral_rewards_awarded"] == 100
    assert result["purchase_rewards_awarded"] == 10
    assert db.get(RewardAccount, 1).balance_points == 160
    assert db.get(RewardAccount, 2).balance_points == 100
    assert post(db, row, order, payment)["referral_rewards_awarded"] == 100
    assert db.query(ReferralCredit).count() == 2
