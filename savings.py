"""Server-owned savings rules and transactional posting, not public endpoints.

Callers own commit/rollback. Row locks serialize all writes per scheme. Never
accept a client balance/rate/weight or use this module as a generic CRUD API.
"""

from calendar import monthrange
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from models import User
from savings_models import (
    SavingsAudit,
    SavingsCheckoutAttempt,
    SavingsEntry,
    SavingsGoldRate,
    SavingsHold,
    SavingsHoldDecision,
    SavingsPayment,
    SavingsPaymentResolution,
    SavingsScheme,
)

MONTHLY_MIN_PAISE = 50_000
DIGIGOLD_MIN_PAISE = 10_000
MICROGRAMS_PER_GRAM = 1_000_000


class SavingsConflict(ValueError):
    pass


def utc(value):
    if not isinstance(value, datetime):
        raise ValueError("Server datetime required")
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def positive_integer(value):
    if type(value) is not int or value <= 0 or value > 9_223_372_036_854_775_807:
        raise ValueError("Positive integer minor-unit amount required")
    return value


def text(value, maximum=100):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Nonempty bounded reference required")
    return value.strip()


def maturity_at(kind, started_at):
    start = utc(started_at)
    if kind == "digigold":
        return start + timedelta(days=330)
    if kind != "monthly":
        raise ValueError("Unknown scheme")
    # Eleven calendar months; retain the time, clamp month-end to its last day.
    india = timezone(timedelta(hours=5, minutes=30))
    local_start = start.replace(tzinfo=timezone.utc).astimezone(india)
    month_index = local_start.year * 12 + local_start.month - 1 + 11
    year, month = divmod(month_index, 12)
    month += 1
    return utc(
        local_start.replace(
            year=year, month=month, day=min(local_start.day, monthrange(year, month)[1])
        )
    )


def bonus_basis_points(started_at, posted_at):
    start, posted = utc(started_at), utc(posted_at)
    if posted < start:
        raise ValueError("Posting precedes enrollment")
    days = (posted - start).days
    for upper, bps in ((75, 500), (150, 375), (225, 200), (300, 75)):
        if days <= upper:
            return bps
    return 0


def gold_credit(amount_paise, paise_per_gram, bonus_bps):
    amount, rate = positive_integer(amount_paise), positive_integer(paise_per_gram)
    if type(bonus_bps) is not int or not 0 <= bonus_bps <= 500:
        raise ValueError("Invalid bonus")
    # Approved: round purchased quantity and its bonus down separately to 1 ug.
    purchased = amount * MICROGRAMS_PER_GRAM // rate
    bonus = purchased * bonus_bps // 10_000
    if purchased + bonus > 9_223_372_036_854_775_807:
        raise ValueError("Gold quantity exceeds accounting range")
    return purchased, bonus


def audit(db, scheme, event_key, action, actor_id, evidence, now):
    db.add(
        SavingsAudit(
            scheme_id=scheme.id,
            event_key=event_key,
            action=action,
            actor_id=actor_id,
            evidence_reference=evidence,
            created_at=now,
        )
    )


def locked_user(db, user_id):
    if db.query(User.id).filter_by(id=user_id).with_for_update().first() is None:
        raise SavingsConflict("Customer unavailable")


def enroll(db, user_id, kind, request_key, now, monthly_paise=None):
    key, now = text(request_key), utc(now)
    if kind == "monthly":
        if positive_integer(monthly_paise) < MONTHLY_MIN_PAISE:
            raise ValueError("Monthly minimum is INR 500")
    elif kind != "digigold" or monthly_paise is not None:
        raise ValueError("Invalid enrollment")
    locked_user(db, user_id)
    existing = db.query(SavingsScheme).filter_by(user_id=user_id, request_key=key).first()
    if existing:
        if (existing.kind, existing.monthly_paise) != (kind, monthly_paise):
            raise SavingsConflict("Enrollment key reused with changed inputs")
        return existing
    row = SavingsScheme(
        user_id=user_id,
        kind=kind,
        request_key=key,
        monthly_paise=monthly_paise,
        started_at=now,
        matures_at=maturity_at(kind, now),
        state="active",
    )
    db.add(row)
    db.flush()
    audit(db, row, f"enroll:{row.id}", "enrolled", user_id, key, now)
    db.flush()
    return row


def latest_hold_decision(db, hold_id):
    return (
        db.query(SavingsHoldDecision)
        .filter_by(hold_id=hold_id)
        .order_by(SavingsHoldDecision.id.desc())
        .first()
    )


def active_holds(db, scheme_id):
    last_action = (
        select(SavingsHoldDecision.action)
        .where(SavingsHoldDecision.hold_id == SavingsHold.id)
        .order_by(SavingsHoldDecision.id.desc())
        .limit(1)
        .correlate(SavingsHold)
        .scalar_subquery()
    )
    return (
        db.query(SavingsHold)
        .filter_by(scheme_id=scheme_id)
        .filter(or_(last_action.is_(None), last_action != "released"))
    )


def locked_scheme(db, scheme_id, user_id=None, *, allow_held=False):
    query = db.query(SavingsScheme).filter_by(id=scheme_id)
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    row = query.populate_existing().with_for_update().first()
    if row is None:
        raise SavingsConflict("Scheme unavailable")
    if not allow_held and active_holds(db, row.id).first():
        raise SavingsConflict("Savings plan is held for store review")
    return row


def payment_request(db, user_id, scheme_id, request_key, mode, now, amount_paise=None):
    key, now = text(request_key), utc(now)
    if mode not in ("manual", "razorpay"):
        raise ValueError("Unsupported payment mode")
    locked_user(db, user_id)
    scheme = locked_scheme(db, scheme_id, user_id)
    amount = scheme.monthly_paise if scheme.kind == "monthly" else positive_integer(amount_paise)
    if amount_paise is not None:
        positive_integer(amount_paise)
    if scheme.kind == "monthly" and amount_paise is not None and amount_paise != amount:
        raise ValueError("Installment amount is server-owned")
    if scheme.kind == "digigold" and amount < DIGIGOLD_MIN_PAISE:
        raise ValueError("DigiGold minimum is INR 100")
    old = db.query(SavingsPayment).filter_by(user_id=user_id, request_key=key).first()
    if old:
        if (old.scheme_id, old.mode, old.amount_paise) != (scheme_id, mode, amount):
            raise SavingsConflict("Payment key reused with changed inputs")
        return old
    if scheme.state != "active" or now < scheme.started_at:
        raise SavingsConflict("Scheme does not accept payments")
    installment = None
    if scheme.kind == "monthly":
        # Do not invent a schedule for advance/late payments: slot identity only.
        count = (
            db.query(func.count(SavingsPayment.id))
            .filter_by(scheme_id=scheme_id)
            .filter(SavingsPayment.state.in_(("pending", "posted")))
            .scalar()
        )
        if count >= 11:
            raise SavingsConflict("All installments already requested")
        if db.query(SavingsPayment.id).filter_by(scheme_id=scheme_id, state="pending").first():
            raise SavingsConflict("An installment is awaiting payment")
        installment = count + 1
    row = SavingsPayment(
        scheme_id=scheme_id,
        user_id=user_id,
        request_key=key,
        amount_paise=amount,
        installment=installment,
        mode=mode,
        state="pending",
        created_at=now,
    )
    db.add(row)
    db.flush()
    audit(db, scheme, f"request:{row.id}", "payment_requested", user_id, key, now)
    db.flush()
    return row


def post(db, payment_id, reference, now, actor_id, mode):
    reference = text(reference)
    payment = db.get(SavingsPayment, payment_id)
    if payment is None:
        raise SavingsConflict("Payment unavailable")
    scheme = locked_scheme(db, payment.scheme_id)
    now = utc(now() if callable(now) else now)
    db.refresh(payment)
    if payment.mode != mode:
        raise SavingsConflict("Confirmation method differs from request")
    if payment.state == "posted":
        if payment.verified_reference != reference:
            raise SavingsConflict("Posted payment has a different reference")
        return db.query(SavingsEntry).filter_by(payment_id=payment.id).one()
    if payment.state != "pending":
        raise SavingsConflict("Payment request has been closed")
    if scheme.state != "active" or now < payment.created_at or now < scheme.started_at:
        raise SavingsConflict("Payment cannot be posted")
    if db.query(SavingsPayment.id).filter_by(mode=mode, verified_reference=reference).first():
        raise SavingsConflict("Payment reference already credited")
    gold, bonus, bps, rate_id = 0, 0, 0, None
    if scheme.kind == "digigold":
        rate = (
            db.query(SavingsGoldRate)
            .filter(
                SavingsGoldRate.purity == "24K 995",
                SavingsGoldRate.effective_at <= now,
                SavingsGoldRate.valid_until > now,
            )
            .order_by(SavingsGoldRate.effective_at.desc(), SavingsGoldRate.id.desc())
            .first()
        )
        if rate is None:
            raise SavingsConflict("No valid server gold rate")
        bps = bonus_basis_points(scheme.started_at, now)
        gold, bonus = gold_credit(payment.amount_paise, rate.paise_per_gram, bps)
        rate_id = rate.id
    principal_total, gold_total, bonus_total = balance(db, scheme.id)
    maximum = 9_223_372_036_854_775_807
    if (
        principal_total + payment.amount_paise > maximum
        or gold_total + bonus_total + gold + bonus > maximum
    ):
        raise ValueError("Savings balance exceeds accounting range")
    entry = SavingsEntry(
        scheme_id=scheme.id,
        event_key=f"payment:{payment.id}",
        payment_id=payment.id,
        kind="credit",
        principal_paise=payment.amount_paise,
        gold_micrograms=gold,
        bonus_micrograms=bonus,
        store_bonus_paise=0,
        rate_id=rate_id,
        bonus_bps=bps,
        actor_id=actor_id,
        evidence_reference=reference,
        created_at=now,
    )
    db.add(entry)
    payment.state, payment.verified_reference, payment.posted_at = "posted", reference, now
    audit(db, scheme, f"post:{payment.id}", "payment_posted", actor_id, reference, now)
    db.flush()
    return entry


def confirm_manual(db, payment_id, admin, reference, now):
    # HTTP adapters additionally require the savings:manage permission.
    if not admin.is_admin:
        raise PermissionError("Admin confirmation required")
    return post(db, payment_id, reference, now, admin.id, "manual")


def verify_razorpay(db, payment_id, user_id, provider_payment_id, fetch_payment, now):
    payment = db.query(SavingsPayment).filter_by(id=payment_id, user_id=user_id).first()
    if payment is None or payment.mode != "razorpay" or not payment.provider_order_id:
        raise SavingsConflict("Owned checkout unavailable")
    provider_id = text(provider_payment_id)
    # No client payment object/fallback: verified server-to-server evidence only.
    status, evidence = fetch_payment(provider_id)
    if status != 200 or not isinstance(evidence, dict):
        raise SavingsConflict("Payment verification unavailable")
    if (
        evidence.get("id") != provider_id
        or evidence.get("order_id") != payment.provider_order_id
        or evidence.get("status") != "captured"
        or evidence.get("currency") != "INR"
        or type(evidence.get("amount")) is not int
        or evidence["amount"] != payment.amount_paise
        or evidence.get("refunded", False) is not False
        or evidence.get("amount_refunded", 0) != 0
    ):
        raise SavingsConflict("Captured payment does not match savings checkout")
    return post(db, payment_id, provider_id, now, None, "razorpay")


def balance(db, scheme_id):
    totals = (
        db.query(
            func.coalesce(func.sum(SavingsEntry.principal_paise), 0),
            func.coalesce(func.sum(SavingsEntry.gold_micrograms), 0),
            func.coalesce(func.sum(SavingsEntry.bonus_micrograms), 0),
        )
        .filter_by(scheme_id=scheme_id)
        .one()
    )
    return tuple(int(value) for value in totals)


def redeem(db, scheme_id, admin, reference, now):
    if not admin.is_admin:
        raise PermissionError("Admin redemption required")
    reference, now = text(reference), utc(now)
    scheme = locked_scheme(db, scheme_id)
    existing = db.query(SavingsEntry).filter_by(event_key=f"redeem:{scheme_id}").first()
    if existing:
        if existing.evidence_reference != reference:
            raise SavingsConflict("Redemption has a different reference")
        return existing
    if scheme.state != "active" or now < scheme.matures_at:
        raise SavingsConflict("Scheme has not matured")
    if db.query(SavingsPayment.id).filter_by(scheme_id=scheme_id, state="pending").first():
        raise SavingsConflict("Resolve pending payments before redemption")
    credits = db.query(SavingsEntry).filter_by(scheme_id=scheme_id, kind="credit").count()
    if scheme.kind == "monthly" and credits != 11:
        raise SavingsConflict("Eleven confirmed installments required")
    principal, gold, bonus = balance(db, scheme_id)
    if principal <= 0:
        raise SavingsConflict("No savings value available")
    entry = SavingsEntry(
        scheme_id=scheme_id,
        event_key=f"redeem:{scheme_id}",
        kind="redemption",
        principal_paise=-principal,
        gold_micrograms=-gold,
        bonus_micrograms=-bonus,
        store_bonus_paise=scheme.monthly_paise if scheme.kind == "monthly" else 0,
        bonus_bps=0,
        actor_id=admin.id,
        evidence_reference=reference,
        created_at=now,
    )
    db.add(entry)
    scheme.state = "redeemed"
    audit(db, scheme, f"redeem:{scheme_id}", "redeemed", admin.id, reference, now)
    db.flush()
    return entry


def resolve_pending(db, payment_id, actor, reason, now, *, kind, admin=False):
    reason = text(reason)
    if kind not in ("cancelled", "rejected") or (kind == "rejected" and not admin):
        raise ValueError("Invalid pending resolution")
    if admin and not actor.is_admin:
        raise PermissionError("Admin review required")
    payment = db.get(SavingsPayment, payment_id)
    if payment is None:
        raise SavingsConflict("Payment unavailable")
    scheme = locked_scheme(db, payment.scheme_id, None if admin else actor.id)
    db.refresh(payment)
    existing = db.get(SavingsPaymentResolution, payment.id)
    if existing:
        if (existing.kind, existing.reason, existing.actor_id) != (kind, reason, actor.id):
            raise SavingsConflict("Request already closed with a different review")
        return payment
    if payment.state != "pending" or scheme.state != "active":
        raise SavingsConflict("Only unconfirmed requests can be closed")
    if (
        payment.provider_order_id
        or payment.verified_reference
        or db.get(SavingsCheckoutAttempt, payment.id)
    ):
        raise SavingsConflict("Checkout started; reconcile payment before closing")
    if not admin and payment.mode != "razorpay":
        raise SavingsConflict("Manual requests require store review")
    now = utc(now() if callable(now) else now)
    if now < payment.created_at:
        raise SavingsConflict("Review precedes payment request")
    db.add(
        SavingsPaymentResolution(
            payment_id=payment.id,
            kind=kind,
            reason=reason,
            original_installment=payment.installment,
            actor_id=actor.id,
            created_at=now,
        )
    )
    payment.state = kind
    # Preserve the old slot in the immutable resolution; allow a fresh request.
    payment.installment = None
    audit(db, scheme, f"resolve:{payment.id}", "payment_" + kind, actor.id, reason, now)
    db.flush()
    return payment


def record_hold(
    db,
    scheme_id,
    kind,
    reference,
    reason,
    now,
    actor_id=None,
    payment_id=None,
    *,
    reopen_released=True,
):
    if kind not in ("refund", "dispute", "admin_review"):
        raise ValueError("Invalid savings review")
    reference, reason = text(reference), text(reason)
    scheme = locked_scheme(db, scheme_id, allow_held=True)
    if payment_id is not None:
        payment = db.get(SavingsPayment, payment_id)
        if payment is None or payment.scheme_id != scheme.id:
            raise SavingsConflict("Review payment does not match plan")
    existing = (
        db.query(SavingsHold).filter_by(scheme_id=scheme.id, kind=kind, reference=reference).first()
    )
    if existing:
        if existing.payment_id != payment_id or existing.reason != reason:
            raise SavingsConflict("Review reference reused with different details")
        decision = latest_hold_decision(db, existing.id)
        if decision and decision.action == "released" and reopen_released:
            import hashlib

            observed_at = utc(now() if callable(now) else now)
            if observed_at < decision.created_at:
                raise SavingsConflict("Review predates last release")
            event_key = f"reopen:{existing.id}:{decision.id}"
            db.add(
                SavingsHoldDecision(
                    hold_id=existing.id,
                    action="reopened",
                    event_key=event_key,
                    reason="New adverse evidence requires review",
                    actor_id=actor_id,
                    evidence_sha256=hashlib.sha256(reference.encode()).hexdigest(),
                    created_at=observed_at,
                )
            )
            audit(db, scheme, event_key, "review_hold_reopened", actor_id, reference, observed_at)
            db.flush()
        return existing
    now = utc(now() if callable(now) else now)
    row = SavingsHold(
        scheme_id=scheme.id,
        payment_id=payment_id,
        kind=kind,
        reference=reference,
        reason=reason,
        actor_id=actor_id,
        created_at=now,
    )
    db.add(row)
    db.flush()
    audit(db, scheme, f"hold:{row.id}", "review_hold_created", actor_id, reference, now)
    db.flush()
    return row
