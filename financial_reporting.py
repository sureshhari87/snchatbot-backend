"""Permission-scoped saved-ledger reporting. Never posts, repairs or contacts a provider."""

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, load_only

import config
from custom_design_balance import accounting
from custom_design_models import CustomDesign, CustomDesignDecision
from custom_design_payments import CustomDesignCheckout, CustomDesignHold
from models import OrderSnapshot, User
from rewards_vouchers_models import (
    GiftVoucher,
    GiftVoucherEntry,
    RewardAccount,
    RewardEntry,
    RewardVoucherHold,
)
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent
from rewards_vouchers_rules import calculate_checkout
from savings import active_holds, bonus_basis_points, gold_credit
from savings_models import (
    SavingsAudit,
    SavingsCheckoutAttempt,
    SavingsEntry,
    SavingsGoldRate,
    SavingsPayment,
    SavingsPaymentResolution,
    SavingsScheme,
)
from voucher_funding_models import VoucherFunding, VoucherFundingEvent

ENABLED = os.getenv("FINANCIAL_REPORTING_ENABLED", "0").strip() == "1"
IST = timezone(timedelta(minutes=330))
AREAS = {
    "merchandise": (
        "support:manage",
        FinancialReservation,
        FinancialReservation.id,
        ["posted_payment_paise", "pending_payment_paise"],
    ),
    "savings": (
        "savings:manage",
        SavingsPayment,
        SavingsPayment.id,
        [
            "posted_principal_paise",
            "pending_principal_paise",
            "gold_micrograms",
            "benefit_micrograms",
        ],
    ),
    "custom_designs": (
        "support:manage",
        CustomDesign,
        CustomDesign.id,
        [
            "posted_advance_paise",
            "posted_balance_paise",
            "pending_advance_paise",
            "pending_balance_paise",
        ],
    ),
    "vouchers": (
        "vouchers:manage",
        GiftVoucher,
        GiftVoucher.id,
        [
            "funding_paise",
            "complimentary_paise",
            "pending_funding_paise",
            "redemption_paise",
            "reserved_paise",
        ],
    ),
    "rewards": (
        "rewards:manage",
        RewardAccount,
        RewardAccount.user_id,
        ["issued_points", "used_points", "reserved_points"],
    ),
}


def require(value):
    if not value:
        raise ValueError("Saved evidence requires review")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def total(query, column):
    value = query.with_entities(func.coalesce(func.sum(column), 0)).scalar()
    require(
        type(value) is int
        or (isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value())
    )
    return int(value)


def one(query):
    rows = query.limit(2).all()
    require(len(rows) == 1)
    return rows[0]


def held(db, user_id=None, voucher_id=None):
    targets = []
    if user_id is not None:
        targets.append(RewardVoucherHold.reward_user_id == user_id)
    if voucher_id is not None:
        targets.append(RewardVoucherHold.voucher_id == voucher_id)
    return bool(targets and db.query(RewardVoucherHold.id).filter(or_(*targets)).first())


def frozen_quote(row):
    quote = json.loads(row.quote_json)
    require(
        isinstance(quote, dict)
        and all(
            type(value) is int and 0 <= value <= 9223372036854775807 for value in quote.values()
        )
    )
    calculated = calculate_checkout(
        merchandise_paise=quote["merchandise_paise"],
        tax_paise=quote["tax_paise"],
        delivery_paise=quote["delivery_paise"],
        coupon_discount_paise=quote["coupon_discount_paise"],
        voucher_available_paise=row.voucher_paise,
        reward_points_available=row.reward_points,
        reward_points_requested=row.reward_points,
    )
    require(asdict(calculated) == quote and calculated.payable_paise == row.payable_paise)
    require(
        calculated.voucher_redemption_paise == row.voucher_paise
        and calculated.reward_points_used == row.reward_points
    )
    return quote


def prepare_proofs(db, rows, context):
    orders, events, reservations = context["orders"], context["events"], context["reservations"]
    order_ids = {row.order_id for row in rows if row.order_id is not None} - orders.keys()
    event_ids = {row.id for row in rows} - events.keys()
    for row in rows:
        if row.order_id is not None:
            linked = reservations.setdefault(row.order_id, [])
            if not any(saved.id == row.id for saved in linked):
                linked.append(row)
    for identity in order_ids:
        orders[identity] = None
    if order_ids:
        saved = (
            db.query(OrderSnapshot)
            .options(
                load_only(
                    OrderSnapshot.id,
                    OrderSnapshot.user_id,
                    OrderSnapshot.currency,
                    OrderSnapshot.order_reference,
                    OrderSnapshot.payment_reference,
                    OrderSnapshot.payment_status,
                )
            )
            .filter(OrderSnapshot.id.in_(order_ids))
            .all()
        )
        orders.update({row.id: row for row in saved})
    for identity in event_ids:
        events[identity] = []
    if event_ids:
        saved = (
            db.query(FinancialReservationEvent)
            .filter(
                FinancialReservationEvent.reservation_id.in_(event_ids),
                FinancialReservationEvent.action == "payment_posted",
            )
            .all()
        )
        for row in saved:
            events[row.reservation_id].append(row)


def load_order_proofs(db, order_ids, context):
    missing = set(order_ids) - context["reservations"].keys()
    for identity in missing:
        context["reservations"][identity] = []
    if missing:
        rows = (
            db.query(FinancialReservation)
            .options(
                load_only(
                    FinancialReservation.id,
                    FinancialReservation.order_id,
                    FinancialReservation.user_id,
                    FinancialReservation.state,
                    FinancialReservation.provider_order_id,
                    FinancialReservation.provider_payment_id,
                    FinancialReservation.quote_json,
                    FinancialReservation.payable_paise,
                    FinancialReservation.voucher_id,
                    FinancialReservation.voucher_paise,
                    FinancialReservation.reward_points,
                )
            )
            .filter(FinancialReservation.order_id.in_(missing))
            .all()
        )
        prepare_proofs(db, rows, context)


def reservation_proof(db, row, context):
    require(
        row is not None
        and row.state == "posted"
        and row.provider_payment_id
        and row.provider_order_id
    )
    if row.id in context["proofs"]:
        require(context["proofs"][row.id] is not None)
        return context["proofs"][row.id]
    context["proofs"][row.id] = None
    prepare_proofs(db, [row], context)
    quote = frozen_quote(row)
    order = context["orders"].get(row.order_id)
    require(order is not None and order.user_id == row.user_id and order.currency == "INR")
    require(
        order.order_reference == row.provider_order_id
        and order.payment_reference == row.provider_payment_id
    )
    require(order.payment_status in {"verified", "paid"})
    evidence = digest(
        dict(
            reservation_id=row.id,
            order_id=row.order_id,
            payment_id=row.provider_payment_id,
            quote=quote,
        )
    )
    events = context["events"].get(row.id, [])
    require(len(events) == 1)
    require(
        events[0].evidence_sha256
        == digest(dict(payment_id=row.provider_payment_id, evidence_sha256=evidence))
    )
    context["proofs"][row.id] = evidence, quote
    return evidence, quote


def merchandise(db, row, context):
    frozen_quote(row)
    measures = dict(posted_payment_paise=0, pending_payment_paise=0)
    if row.state == "posted":
        reservation_proof(db, row, context)
        measures["posted_payment_paise"] = row.payable_paise
        status = "posted"
    else:
        require(not context["events"].get(row.id))
        measures["pending_payment_paise"] = row.payable_paise
        status = "pending"
    return measures, status, held(db, row.user_id, row.voucher_id)


def savings(db, row, context):
    scheme = db.get(SavingsScheme, row.scheme_id)
    require(scheme is not None and row.user_id == scheme.user_id)
    hold = active_holds(db, scheme.id).count() > 0
    attempt = db.get(SavingsCheckoutAttempt, row.id)
    if row.mode == "manual":
        require(row.provider_order_id is None and attempt is None)
    else:
        require(row.mode == "razorpay")
        if row.provider_order_id:
            require(attempt is not None and attempt.state == "ready")
        if attempt and attempt.state == "ready":
            require(row.provider_order_id)
        if row.state == "posted":
            require(row.provider_order_id and attempt is not None and attempt.state == "ready")
    entries = db.query(SavingsEntry).filter_by(payment_id=row.id)
    audit = db.query(SavingsAudit).filter_by(event_key=f"post:{row.id}", action="payment_posted")
    measures = dict(
        posted_principal_paise=0, pending_principal_paise=0, gold_micrograms=0, benefit_micrograms=0
    )
    if row.state == "posted":
        entry, event = one(entries), one(audit)
        require(entry.kind == "credit" and entry.scheme_id == scheme.id)
        require(entry.principal_paise == row.amount_paise and row.verified_reference)
        require(entry.evidence_reference == row.verified_reference == event.evidence_reference)
        require(
            entry.created_at == row.posted_at == event.created_at and event.scheme_id == scheme.id
        )
        require(event.actor_id == entry.actor_id)
        if row.mode == "manual":
            require(entry.actor_id is not None)
        if scheme.kind == "monthly":
            require(row.amount_paise == scheme.monthly_paise and row.installment in range(1, 12))
            require(
                entry.gold_micrograms == entry.bonus_micrograms == entry.bonus_bps == 0
                and entry.rate_id is None
            )
        else:
            rate = db.get(SavingsGoldRate, entry.rate_id) if entry.rate_id else None
            require(
                rate is not None
                and rate.purity == "24K 995"
                and rate.effective_at <= entry.created_at < rate.valid_until
            )
            bps = bonus_basis_points(scheme.started_at, entry.created_at)
            gold, benefit = gold_credit(row.amount_paise, rate.paise_per_gram, bps)
            require(
                (entry.gold_micrograms, entry.bonus_micrograms, entry.bonus_bps)
                == (gold, benefit, bps)
            )
        measures.update(
            posted_principal_paise=entry.principal_paise,
            gold_micrograms=entry.gold_micrograms,
            benefit_micrograms=entry.bonus_micrograms,
        )
        status = "posted"
    else:
        require(
            not entries.first()
            and not audit.first()
            and not row.verified_reference
            and row.posted_at is None
        )
        status = "pending" if row.state == "pending" else "closed"
        if status == "pending":
            if scheme.kind == "monthly":
                require(
                    row.amount_paise == scheme.monthly_paise and row.installment in range(1, 12)
                )
            measures["pending_principal_paise"] = row.amount_paise
        else:
            resolution = db.get(SavingsPaymentResolution, row.id)
            require(resolution is not None and resolution.kind == row.state)
            require(row.installment is None and not row.provider_order_id and attempt is None)
    return measures, status, hold


def custom_designs(db, row, context):
    quote, advance, request, entry, _, _ = accounting(db, row)
    measures = dict(
        posted_advance_paise=advance.amount_paise if advance else 0,
        posted_balance_paise=entry.amount_paise if entry else 0,
        pending_advance_paise=0,
        pending_balance_paise=0,
    )
    checkout = db.query(CustomDesignCheckout).filter_by(design_id=row.id).first()
    if checkout or advance or request or entry:
        require(quote is not None)
        decision = (
            db.query(CustomDesignDecision)
            .filter_by(quote_id=quote.id)
            .order_by(CustomDesignDecision.id.desc())
            .first()
        )
        require(decision is not None and decision.action == "approved")
    if checkout:
        require(checkout.quote_id == quote.id)
    if checkout and not advance:
        require(checkout.design_id == row.id)
        measures["pending_advance_paise"] = quote.advance_paise
    if request and not entry:
        measures["pending_balance_paise"] = request.amount_paise
    status = (
        "pending"
        if measures["pending_advance_paise"] or measures["pending_balance_paise"]
        else ("posted" if advance or entry else "not_started")
    )
    return (
        measures,
        status,
        db.query(CustomDesignHold.id).filter_by(design_id=row.id).first() is not None,
    )


def redemption_proof(db, entry, context, user_id=None):
    load_order_proofs(db, [entry.order_id], context)
    linked = context["reservations"].get(entry.order_id, [])
    require(len(linked) == 1)
    reservation = linked[0]
    if user_id is not None:
        require(reservation.user_id == user_id)
    evidence, quote = reservation_proof(db, reservation, context)
    require(
        entry.evidence_reference == reservation.provider_payment_id
        and entry.evidence_sha256 == evidence
    )
    return reservation, quote


def vouchers(db, row, context):
    entries = (
        db.query(GiftVoucherEntry)
        .filter_by(voucher_id=row.id)
        .order_by(GiftVoucherEntry.id)
        .limit(1001)
        .all()
    )
    require(len(entries) <= 1000)
    funding = db.get(VoucherFunding, row.id)
    require(funding is not None)
    require(
        row.source == ("complimentary" if funding.payment_mode == "complimentary" else "purchased")
    )
    credits = [entry for entry in entries if entry.kind != "redemption"]
    events = db.query(VoucherFundingEvent).filter_by(voucher_id=row.id, action="funding_posted")
    measures = dict(
        funding_paise=0,
        complimentary_paise=0,
        pending_funding_paise=0,
        redemption_paise=0,
        reserved_paise=0,
    )
    require(sum(entry.amount_delta_paise for entry in entries) == row.balance_paise)
    reserved = total(
        db.query(FinancialReservation).filter(
            FinancialReservation.voucher_id == row.id, FinancialReservation.state != "posted"
        ),
        FinancialReservation.voucher_paise,
    )
    require(reserved == row.reserved_paise)
    if row.state == "active":
        require(funding.state == "posted" and len(credits) == 1)
        entry, event = credits[0], one(events)
        require(
            entry.event_key == f"voucher-fund:{row.id}"
            and entry.amount_delta_paise == row.amount_paise
        )
        require(event.actor_id == entry.actor_id and event.evidence_sha256 == entry.evidence_sha256)
        if funding.payment_mode == "razorpay":
            require(entry.kind == "razorpay_funding" and entry.actor_id is None)
            require(
                funding.provider_payment_id == entry.evidence_reference
                and funding.provider_order_id
            )
            evidence = dict(
                voucher_id=row.id,
                payment_id=funding.provider_payment_id,
                provider_order_id=funding.provider_order_id,
                amount_paise=row.amount_paise,
            )
        else:
            expected_kind = (
                "complimentary" if funding.payment_mode == "complimentary" else "manual_funding"
            )
            require(entry.kind == expected_kind and entry.actor_id is not None and entry.reason)
            evidence = dict(
                voucher_id=row.id,
                amount_paise=row.amount_paise,
                actor_id=entry.actor_id,
                reference=entry.evidence_reference,
                reason=entry.reason,
                kind=entry.kind,
            )
        require(entry.evidence_sha256 == digest(evidence))
        measures["complimentary_paise" if entry.kind == "complimentary" else "funding_paise"] = (
            entry.amount_delta_paise
        )
        status = "posted"
    else:
        require(funding.state != "posted" and not credits and not events.first())
        measures["pending_funding_paise"] = row.amount_paise
        status = "pending"
    load_order_proofs(
        db, [entry.order_id for entry in entries if entry.kind == "redemption"], context
    )
    for entry in entries:
        if entry.kind == "redemption":
            reservation, _ = redemption_proof(db, entry, context)
            require(
                reservation.voucher_id == row.id
                and entry.amount_delta_paise == -reservation.voucher_paise
            )
            measures["redemption_paise"] -= entry.amount_delta_paise
    measures["reserved_paise"] = reserved
    return measures, status, held(db, voucher_id=row.id)


def rewards(db, row, context):
    entries = (
        db.query(RewardEntry)
        .filter_by(user_id=row.user_id)
        .order_by(RewardEntry.id)
        .limit(1001)
        .all()
    )
    import referrals

    referral_entries = referrals.credits(db, row.user_id)
    require(
        len(entries) + len(referral_entries) <= 1000
        and sum(entry.points_delta for entry in entries + referral_entries) == row.balance_points
    )
    reserved = total(
        db.query(FinancialReservation).filter(
            FinancialReservation.user_id == row.user_id, FinancialReservation.state != "posted"
        ),
        FinancialReservation.reward_points,
    )
    require(reserved == row.reserved_points)
    measures = dict(issued_points=0, used_points=0, reserved_points=reserved)
    load_order_proofs(db, [entry.order_id for entry in entries + referral_entries], context)
    for entry in entries:
        reservation, quote = redemption_proof(db, entry, context, row.user_id)
        if entry.kind == "purchase_credit":
            require(
                type(quote.get("reward_points_earned")) is int
                and entry.points_delta == quote["reward_points_earned"]
            )
            measures["issued_points"] += entry.points_delta
        else:
            require(entry.kind == "redemption" and entry.points_delta == -reservation.reward_points)
            measures["used_points"] -= entry.points_delta
    for entry in referral_entries:
        application, outcome = referrals.validate_credit(db, entry)
        reservation, quote = redemption_proof(db, entry, context)
        require(
            reservation.user_id == application.referred_id
            and outcome.merchandise_after_coupon_paise
            == quote["merchandise_paise"] - quote["coupon_discount_paise"]
            and outcome.merchandise_after_coupon_paise >= application.minimum_merchandise_paise
        )
        measures["issued_points"] += entry.points_delta
    return (
        measures,
        "posted" if entries or referral_entries else "not_started",
        held(db, row.user_id),
    )


BUILDERS = dict(
    merchandise=merchandise,
    savings=savings,
    custom_designs=custom_designs,
    vouchers=vouchers,
    rewards=rewards,
)


def report(db, area, after=0, limit=20):
    _, model, identity, fields = AREAS[area]
    # No session flush, repair helper, provider fetch, lock, commit or mutation.
    with db.no_autoflush:
        rows = db.query(model).filter(identity > after).order_by(identity).limit(limit + 1).all()
        items = []
        context = {"orders": {}, "events": {}, "reservations": {}, "proofs": {}}
        if area == "merchandise":
            prepare_proofs(db, rows[:limit], context)
        for row in rows[:limit]:
            if area in {"vouchers", "rewards"}:
                # Bound private proof memory to one account, not the whole page.
                context = {"orders": {}, "events": {}, "reservations": {}, "proofs": {}}
            record_id = getattr(row, identity.key)
            item = dict(
                id=record_id,
                status="requires_review",
                held=None,
                measures={field: None for field in fields},
                issue_codes=[],
                evidence_valid=False,
            )
            try:
                measures, status, hold = BUILDERS[area](db, row, context)
                require(
                    set(measures) == set(fields)
                    and all(type(value) is int and value >= 0 for value in measures.values())
                )
                item.update(
                    measures={key: str(value) for key, value in measures.items()},
                    status=status,
                    held=hold,
                    evidence_valid=True,
                )
            except (HTTPException, ValueError, TypeError, KeyError, OverflowError):
                item["issue_codes"] = ["saved_evidence_requires_review"]
            items.append(item)
        legacy_count = None
        if area == "merchandise":
            legacy_count = (
                db.query(OrderSnapshot.id)
                .filter(
                    ~db.query(FinancialReservation.id)
                    .filter(FinancialReservation.order_id == OrderSnapshot.id)
                    .exists()
                )
                .count()
            )
    return dict(
        area=area,
        as_of_ist=datetime.now(timezone.utc).astimezone(IST).isoformat(),
        items=items,
        next_after=getattr(rows[limit - 1], identity.key) if len(rows) > limit else None,
        scope="listed_records_only",
        measure_encoding="integer_decimal_strings",
        legacy_orders_requiring_review=legacy_count,
        provider_verified=False,
        read_only=True,
        release_authorized=False,
    )


def enabled():
    if not ENABLED:
        raise HTTPException(503, "Financial reconciliation reporting is not enabled")
    if config.APP_ENV not in {"staging", "test"}:
        raise HTTPException(403, "Financial reporting rollout is not approved for this environment")


def install(app, get_db, require_permission):
    def register(area, permission):
        def read(
            response: Response,
            after: int = Query(0, ge=0, le=2147483647),
            limit: int = Query(20, ge=1, le=50),
            actor: User = Depends(require_permission(permission)),
            db: Session = Depends(get_db),
        ):
            response.headers["Cache-Control"] = "no-store"
            try:
                return report(db, area, after, limit)
            except SQLAlchemyError:
                raise HTTPException(503, "Financial report unavailable; reload later") from None

        app.get(
            "/admin/financial-reconciliation/" + area,
            dependencies=[Depends(enabled)],
            name="financial_reconciliation_" + area,
        )(read)

    for area, (permission, _, _, _) in AREAS.items():
        register(area, permission)
