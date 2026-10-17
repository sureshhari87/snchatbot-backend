"""Default-off referral transactions. Never commits, calls a provider or releases holds."""

import json
import re
import secrets

from sqlalchemy import func, inspect, or_

from config import get_bool
from models import OrderSnapshot, User, utc_now
from referral_models import ReferralApplication, ReferralCode, ReferralCredit, ReferralOutcome
from rewards_vouchers_models import RewardAccount, RewardVoucherHold
from rewards_vouchers_reservation_models import FinancialReservation
from rewards_vouchers_transactions import account, locked_user, require

ENABLED = get_bool("REFERRALS_ENABLED", False)
MAX_POINTS = 9223372036854775807


def schema_available(db):
    key = "sona_referral_schema_available"
    if key not in db.info:
        db.info[key] = inspect(db.connection()).has_table("referral_applications")
    return db.info[key]


def enabled():
    require(ENABLED, "Referral programme is not enabled", 503)


def customer(db, user_id):
    user = locked_user(db, user_id)
    require(not user.is_admin, "Customer account required", 403)
    return user


def previous_purchase(db, user_id, exclude_order=None):
    orders = db.query(OrderSnapshot.id).filter(
        OrderSnapshot.user_id == user_id,
        or_(
            OrderSnapshot.payment_status.in_(["paid", "captured", "verified"]),
            OrderSnapshot.payment_reference.is_not(None),
        ),
    )
    posted = db.query(FinancialReservation.id).filter_by(user_id=user_id, state="posted")
    if exclude_order is not None:
        orders = orders.filter(OrderSnapshot.id != exclude_order)
        posted = posted.filter(FinancialReservation.order_id != exclude_order)
    return orders.first() is not None or posted.first() is not None


def code_for(db, user_id):
    enabled()
    customer(db, user_id)
    row = db.get(ReferralCode, user_id)
    if row is None:
        row = ReferralCode(
            owner_id=user_id, code="SONA" + secrets.token_hex(6).upper(), created_at=utc_now()
        )
        db.add(row)
        db.flush()
    return row


def apply(db, user_id, code):
    enabled()
    require(isinstance(code, str), "Enter a valid referral code", 422)
    code = code.strip().upper()
    require(re.fullmatch(r"[A-Z0-9]{8,20}", code) is not None, "Enter a valid referral code", 422)
    owner = db.query(ReferralCode).filter_by(code=code).first()
    require(owner is not None, "Referral code unavailable", 404)
    require(owner.owner_id != user_id, "You cannot use your own referral code")
    # Both user locks precede account locks; settlement never locks the referrer user.
    for identity in sorted([user_id, owner.owner_id]):
        customer(db, identity)
    old = db.query(ReferralApplication).filter_by(referred_id=user_id).first()
    if old:
        require(
            old.code == code and old.referrer_id == owner.owner_id,
            "A referral code is already applied",
        )
        return old
    require(
        not previous_purchase(db, user_id), "Apply before your first verified merchandise order"
    )
    for identity in sorted([user_id, owner.owner_id]):
        account(db, identity)
    row = ReferralApplication(
        referred_id=user_id,
        referrer_id=owner.owner_id,
        code=code,
        terms_version=1,
        referrer_points=100,
        customer_points=50,
        minimum_merchandise_paise=100000,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def lock_accounts(db, application):
    result = {}
    for identity in sorted([application.referred_id, application.referrer_id]):
        row = (
            db.query(RewardAccount)
            .filter_by(user_id=identity)
            .populate_existing()
            .with_for_update()
            .first()
        )
        require(row is not None, "Referral reward account requires audited review")
        result[identity] = row
    return result


def prepare(db, user_id):
    if not schema_available(db):
        require(not ENABLED, "Referral schema is not available", 503)
        return None
    row = db.query(ReferralApplication).filter_by(referred_id=user_id).first()
    if row is None or db.query(ReferralOutcome.id).filter_by(application_id=row.id).first():
        return None
    return row, lock_accounts(db, row)


def post(db, context, reservation, payment, evidence):
    if context is None:
        return 0
    application, accounts = context
    require(application.referred_id == reservation.user_id, "Referral owner differs")
    require(
        all(0 <= row.balance_points <= MAX_POINTS for row in accounts.values()),
        "Referral reward balance requires audited review",
    )
    old = db.query(ReferralOutcome).filter_by(application_id=application.id).first()
    if old is not None:
        return 0
    quote = json.loads(reservation.quote_json)
    amount = quote["merchandise_paise"] - quote["coupon_discount_paise"]
    prior = previous_purchase(db, reservation.user_id, reservation.order_id)
    held = db.query(RewardVoucherHold.id).filter(
        RewardVoucherHold.reward_user_id.in_(list(accounts))
    ).first() is not None or any(db.get(User, identity).is_admin for identity in accounts)
    if prior:
        state, reason = "not_qualified", "previous_verified_order"
    elif amount < application.minimum_merchandise_paise:
        state, reason = "not_qualified", "minimum_not_met"
    elif not ENABLED:
        state, reason = "not_qualified", "programme_disabled"
    elif held:
        state, reason = "held", "reward_account_review"
    else:
        state, reason = "rewarded", "first_verified_order"
    outcome = ReferralOutcome(
        application_id=application.id,
        order_id=reservation.order_id,
        state=state,
        reason=reason,
        merchandise_after_coupon_paise=amount,
        evidence_reference=payment["id"],
        evidence_sha256=evidence,
        created_at=utc_now(),
    )
    db.add(outcome)
    db.flush()
    if state != "rewarded":
        return 0
    for identity, role, points in [
        (application.referrer_id, "referrer", application.referrer_points),
        (application.referred_id, "new_customer", application.customer_points),
    ]:
        target = accounts[identity]
        require(
            0 <= target.balance_points <= MAX_POINTS - points,
            "Referral reward balance requires audited review",
        )
        target.balance_points += points
        db.add(
            ReferralCredit(
                outcome_id=outcome.id,
                user_id=identity,
                order_id=reservation.order_id,
                role=role,
                points_delta=points,
                event_key=f"referral:{outcome.id}:{role}",
                evidence_reference=payment["id"],
                evidence_sha256=evidence,
                created_at=utc_now(),
            )
        )
    db.flush()
    return application.customer_points


def credits(db, user_id):
    if not schema_available(db):
        return []
    return (
        db.query(ReferralCredit)
        .filter_by(user_id=user_id)
        .order_by(ReferralCredit.id)
        .limit(1001)
        .all()
    )


def validate_credit(db, entry):
    outcome = db.get(ReferralOutcome, entry.outcome_id)
    require(outcome is not None and outcome.state == "rewarded", "Referral outcome requires review")
    application = db.get(ReferralApplication, outcome.application_id)
    require(application is not None, "Referral application requires review")
    expected_user, expected_points = (
        (application.referrer_id, application.referrer_points)
        if entry.role == "referrer"
        else (application.referred_id, application.customer_points)
    )
    require(
        entry.user_id == expected_user
        and entry.points_delta == expected_points
        and entry.order_id == outcome.order_id
        and entry.evidence_reference == outcome.evidence_reference
        and entry.evidence_sha256 == outcome.evidence_sha256,
        "Referral credit evidence differs",
    )
    require(
        entry.role in ("referrer", "new_customer")
        and entry.event_key == f"referral:{outcome.id}:{entry.role}",
        "Referral event identity differs",
    )
    siblings = db.query(ReferralCredit).filter_by(outcome_id=outcome.id).all()
    require(
        len(siblings) == 2
        and {(x.role, x.user_id, x.points_delta) for x in siblings}
        == {
            ("referrer", application.referrer_id, application.referrer_points),
            ("new_customer", application.referred_id, application.customer_points),
        },
        "Referral paired credits differ",
    )
    require(
        all(
            x.order_id == outcome.order_id
            and x.evidence_reference == outcome.evidence_reference
            and x.evidence_sha256 == outcome.evidence_sha256
            for x in siblings
        ),
        "Referral paired evidence differs",
    )
    return application, outcome


def propagate_holds(db, reservation, adverse):
    # Existing credited referrals remain protected even after programme disablement.
    if not schema_available(db):
        return
    outcome = (
        db.query(ReferralOutcome).filter_by(order_id=reservation.order_id, state="rewarded").first()
    )
    if outcome is None:
        return
    application = db.get(ReferralApplication, outcome.application_id)
    require(
        application is not None and application.referred_id == reservation.user_id,
        "Referral hold owner differs",
    )
    locked_user(db, reservation.user_id)
    accounts = lock_accounts(db, application)
    for identity in sorted(accounts):
        for kind, reference, reason in adverse:
            require(
                kind in ("refund", "dispute")
                and isinstance(reference, str)
                and 1 <= len(reference) <= 100
                and isinstance(reason, str)
                and 1 <= len(reason.strip()) <= 200,
                "Invalid referral hold evidence",
            )
            old = (
                db.query(RewardVoucherHold)
                .filter_by(reward_user_id=identity, kind=kind, reference=reference)
                .first()
            )
            if old:
                require(
                    old.reason == reason.strip() and old.actor_id is None, "Hold evidence differs"
                )
            else:
                db.add(
                    RewardVoucherHold(
                        reward_user_id=identity,
                        kind=kind,
                        reference=reference,
                        reason=reason.strip(),
                        created_at=utc_now(),
                    )
                )
    db.flush()


def programme(db, user_id):
    enabled()
    user = db.get(User, user_id)
    require(user is not None and not user.is_admin, "Customer account required", 403)
    own = db.get(ReferralCode, user_id)
    application = db.query(ReferralApplication).filter_by(referred_id=user_id).first()
    outcome = (
        db.query(ReferralOutcome).filter_by(application_id=application.id).first()
        if application
        else None
    )
    review = db.query(RewardVoucherHold.id).filter_by(reward_user_id=user_id).first() is not None
    status = (
        ("held" if review and outcome and outcome.state == "rewarded" else outcome.state)
        if outcome
        else ("pending" if application else None)
    )
    invited = db.query(ReferralApplication.id).filter_by(referrer_id=user_id).count()
    rewarded = db.query(ReferralCredit).filter_by(user_id=user_id, role="referrer")
    return dict(
        enabled=True,
        code=own.code if own else "",
        referrerRewardPoints=100,
        newCustomerRewardPoints=50,
        minimumOrderAmount=1000,
        appliedCode=application.code if application else None,
        applicationStatus=status,
        reviewRequired=review,
        invitedCount=invited,
        rewardedCount=rewarded.count(),
        pointsEarned=db.query(func.coalesce(func.sum(ReferralCredit.points_delta), 0))
        .filter(ReferralCredit.user_id == user_id, ReferralCredit.role == "referrer")
        .scalar(),
    )


def audit(db, after=0, limit=20):
    """Bounded saved-evidence review, available after programme disablement."""
    from fastapi import HTTPException

    from financial_reporting import redemption_proof

    require(schema_available(db), "Referral schema is not available", 503)
    with db.no_autoflush:
        rows = (
            db.query(ReferralApplication)
            .filter(ReferralApplication.id > after)
            .order_by(ReferralApplication.id)
            .limit(limit + 1)
            .all()
        )
        selected = rows[:limit]
        identities = [row.id for row in selected]
        outcomes = (
            db.query(ReferralOutcome).filter(ReferralOutcome.application_id.in_(identities)).all()
            if identities
            else []
        )
        outcome_map = {row.application_id: row for row in outcomes}
        entries = (
            db.query(ReferralCredit)
            .filter(ReferralCredit.outcome_id.in_([row.id for row in outcomes]))
            .all()
            if outcomes
            else []
        )
        users = {identity for row in selected for identity in (row.referred_id, row.referrer_id)}
        held = (
            {
                identity
                for (identity,) in db.query(RewardVoucherHold.reward_user_id)
                .filter(RewardVoucherHold.reward_user_id.in_(users))
                .distinct()
            }
            if users
            else set()
        )
        context = {"orders": {}, "events": {}, "reservations": {}, "proofs": {}}
        items = []
        for row in selected:
            outcome = outcome_map.get(row.id)
            credits_for_outcome = [
                entry for entry in entries if outcome and entry.outcome_id == outcome.id
            ]
            review = (
                row.referred_id in held
                or row.referrer_id in held
                or outcome is not None
                and outcome.state == "held"
            )
            item = dict(
                id=row.id,
                referred_customer_id=row.referred_id,
                referrer_customer_id=row.referrer_id,
                referrer_points=row.referrer_points,
                customer_points=row.customer_points,
                minimum_merchandise_paise=row.minimum_merchandise_paise,
                posting_state=outcome.state if outcome else "pending",
                reason=outcome.reason if outcome else None,
                review_required=review,
                credit_count=len(credits_for_outcome),
                evidence_valid=True,
                issue_codes=[],
                created_at=row.created_at,
            )
            try:
                if outcome:
                    reservation, quote = redemption_proof(db, outcome, context, row.referred_id)
                    require(
                        outcome.merchandise_after_coupon_paise
                        == quote["merchandise_paise"] - quote["coupon_discount_paise"],
                        "Referral amount differs",
                    )
                    if outcome.state == "rewarded":
                        require(len(credits_for_outcome) == 2, "Referral pair missing")
                        validate_credit(db, credits_for_outcome[0])
                        require(
                            outcome.merchandise_after_coupon_paise >= row.minimum_merchandise_paise,
                            "Referral minimum differs",
                        )
                    else:
                        require(not credits_for_outcome, "Unposted referral has credits")
            except (HTTPException, ValueError, TypeError, KeyError, OverflowError):
                item.update(evidence_valid=False, issue_codes=["saved_evidence_requires_review"])
            items.append(item)
    return dict(
        items=items,
        next_after=selected[-1].id if len(rows) > limit else None,
        read_only=True,
        provider_verified=False,
        release_authorized=False,
    )
