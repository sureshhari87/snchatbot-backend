"""Authoritative billing evidence reads. Never calls providers or changes balances."""

import json
from dataclasses import asdict

from fastapi import HTTPException

from billing_profile import MAX_PAISE
from billing_receipts import iso, require
from custom_design_balance import accounting
from custom_design_models import CustomDesign, CustomDesignDecision
from custom_design_payments import CustomDesignCheckout, CustomDesignHold
from models import OrderSnapshot
from rewards_vouchers_models import GiftVoucher, GiftVoucherEntry, RewardEntry, RewardVoucherHold
from rewards_vouchers_reservation_models import FinancialReservation, FinancialReservationEvent
from rewards_vouchers_rules import calculate_checkout
from rewards_vouchers_transactions import digest
from voucher_funding_models import VoucherFunding, VoucherFundingEvent

SOURCES = {"voucher", "custom_advance", "custom_balance", "merchandise", "custom_design"}


def root(db, source, identity, owner_id=None):
    if source not in SOURCES:
        raise HTTPException(404, "Billing source not found")
    model = (
        GiftVoucher
        if source == "voucher"
        else OrderSnapshot
        if source == "merchandise"
        else CustomDesign
    )
    query = db.query(model).filter_by(id=identity)
    if owner_id is not None:
        query = query.filter_by(**{"purchaser_id" if source == "voucher" else "user_id": owner_id})
    row = query.populate_existing().with_for_update().one_or_none()
    if row is None:
        raise HTTPException(404, "Billing source not found")
    return row


def owner(row, source):
    return row.purchaser_id if source == "voucher" else row.user_id


def review(db, source, row):
    if source == "voucher":
        return db.query(RewardVoucherHold).filter_by(voucher_id=row.id).first() is not None
    if source.startswith("custom"):
        return db.query(CustomDesignHold).filter_by(design_id=row.id).first() is not None
    reservation = db.query(FinancialReservation).filter_by(order_id=row.id).first()
    return bool(
        db.query(RewardVoucherHold).filter_by(reward_user_id=row.user_id).first()
        or (
            reservation
            and reservation.voucher_id
            and db.query(RewardVoucherHold).filter_by(voucher_id=reservation.voucher_id).first()
        )
    )


def payment(amount, mode, reference, posted_at):
    require(
        type(amount) is int
        and 0 < amount <= MAX_PAISE
        and bool(reference)
        and posted_at is not None
    )
    return dict(
        amount_paise=amount,
        currency="INR",
        mode=mode,
        reference=reference,
        posted_at=iso(posted_at),
        status_at_issue="posted",
    )


def evidence(db, source, row):
    if source == "voucher":
        funding = db.get(VoucherFunding, row.id)
        entries = (
            db.query(GiftVoucherEntry)
            .filter(GiftVoucherEntry.voucher_id == row.id, GiftVoucherEntry.kind != "redemption")
            .all()
        )
        audits = (
            db.query(VoucherFundingEvent)
            .filter_by(voucher_id=row.id, action="funding_posted")
            .all()
        )
        require(
            row.source == "purchased"
            and row.state == "active"
            and funding is not None
            and funding.state == "posted"
        )
        require(len(entries) == 1 and len(audits) == 1)
        entry = entries[0]
        require(
            entry.amount_delta_paise == row.amount_paise
            and entry.evidence_sha256 == audits[0].evidence_sha256
        )
        require(
            entry.kind
            == {"manual": "manual_funding", "razorpay": "razorpay_funding"}.get(
                funding.payment_mode
            )
        )
        require(
            funding.payment_mode != "razorpay"
            or (
                funding.provider_order_id
                and funding.provider_payment_id == entry.evidence_reference
            )
        )
        require(entry.created_at == row.activated_at)
        require(entry.actor_id == audits[0].actor_id and bool(entry.evidence_reference))
        return dict(
            payment=payment(
                entry.amount_delta_paise,
                funding.payment_mode,
                entry.evidence_reference,
                entry.created_at,
            ),
            detail=dict(
                voucher_id=row.id,
                denomination_paise=row.amount_paise,
                expires_at=iso(row.expires_at),
            ),
            gross_paise=row.amount_paise,
            paid_paise=row.amount_paise,
            tender=dict(cash_paise=row.amount_paise, voucher_paise=0, rewards_paise=0),
        )
    if source.startswith("custom"):
        quote, advance, request, balance, paid, due = accounting(db, row)
        require(quote is not None)
        decision = (
            db.query(CustomDesignDecision)
            .filter_by(design_id=row.id, quote_id=quote.id)
            .order_by(CustomDesignDecision.id.desc())
            .first()
        )
        require(decision is not None and decision.action == "approved")

        selected = advance if source == "custom_advance" else balance
        if source == "custom_design":
            require(paid == quote.total_paise and due == 0)
            return dict(
                gross_paise=quote.total_paise,
                paid_paise=paid,
                tender=dict(cash_paise=paid, voucher_paise=0, rewards_paise=0),
                detail=dict(
                    design_id=row.id,
                    quote_id=quote.id,
                    quote_version=quote.version,
                    purity=row.purity,
                    customer_request=row.customer_notes or None,
                    accepted_quote_notes=quote.notes,
                    quote_accepted_at=iso(decision.created_at),
                    quote_total_paise=quote.total_paise,
                ),
            )
        require(selected is not None)
        if source == "custom_advance":
            checkout = db.get(CustomDesignCheckout, quote.id)
            require(
                checkout is not None
                and checkout.provider_order_id == selected.provider_order_id
                and checkout.design_id == row.id
            )
        amount = selected.amount_paise
        # Freeze only amounts posted at this payment's time, not later receipts.
        previously = advance.amount_paise if source == "custom_balance" and advance else 0
        return dict(
            payment=payment(
                amount,
                "razorpay" if source == "custom_advance" else selected.mode,
                selected.provider_payment_id if source == "custom_advance" else selected.reference,
                selected.created_at,
            ),
            detail=dict(
                design_id=row.id,
                quote_id=quote.id,
                quote_version=quote.version,
                purity=row.purity,
                customer_request=row.customer_notes or None,
                accepted_quote_notes=quote.notes,
                quote_accepted_at=iso(decision.created_at),
                quote_total_paise=quote.total_paise,
                previously_received_paise=previously,
                received_through_payment_paise=previously + amount,
                remaining_at_payment_paise=quote.total_paise - previously - amount,
            ),
            gross_paise=quote.total_paise,
            paid_paise=paid,
            tender=dict(cash_paise=paid, voucher_paise=0, rewards_paise=0),
        )
    reservation = db.query(FinancialReservation).filter_by(order_id=row.id).one_or_none()
    require(reservation is not None and reservation.state == "posted" and row.currency == "INR")
    require(
        reservation.user_id == row.user_id
        and reservation.provider_order_id == row.order_reference
        and reservation.provider_payment_id == row.payment_reference
        and row.payment_status == "verified"
    )
    audits = (
        db.query(FinancialReservationEvent)
        .filter_by(reservation_id=reservation.id, action="payment_posted")
        .all()
    )
    require(len(audits) == 1)
    try:
        quote = json.loads(reservation.quote_json)
        recomputed = asdict(
            calculate_checkout(
                merchandise_paise=quote["merchandise_paise"],
                tax_paise=quote["tax_paise"],
                delivery_paise=quote["delivery_paise"],
                coupon_discount_paise=quote["coupon_discount_paise"],
                voucher_available_paise=reservation.voucher_paise,
                reward_points_available=reservation.reward_points,
                reward_points_requested=reservation.reward_points,
            )
        )
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "Frozen order quote requires billing review") from None
    require(recomputed == quote and reservation.payable_paise == quote["payable_paise"])
    evidence_hash = digest(
        dict(
            reservation_id=reservation.id,
            order_id=row.id,
            payment_id=row.payment_reference,
            quote=quote,
        )
    )
    require(
        audits[0].evidence_sha256
        == digest(dict(payment_id=row.payment_reference, evidence_sha256=evidence_hash))
    )
    if reservation.voucher_paise:
        used = (
            db.query(GiftVoucherEntry)
            .filter_by(order_id=row.id, voucher_id=reservation.voucher_id, kind="redemption")
            .one_or_none()
        )
        require(
            used is not None
            and used.amount_delta_paise == -reservation.voucher_paise
            and used.evidence_reference == row.payment_reference
            and used.evidence_sha256 == evidence_hash
        )
    if reservation.reward_points:
        used = (
            db.query(RewardEntry)
            .filter_by(order_id=row.id, user_id=row.user_id, kind="redemption")
            .one_or_none()
        )
        require(
            used is not None
            and used.points_delta == -reservation.reward_points
            and used.evidence_reference == row.payment_reference
            and used.evidence_sha256 == evidence_hash
        )
    gross = reservation.payable_paise + reservation.voucher_paise + quote["reward_discount_paise"]
    return dict(
        payment=payment(
            reservation.payable_paise, "razorpay", row.payment_reference, audits[0].created_at
        ),
        detail=dict(order_id=row.id, order_reference=row.order_reference, **quote),
        gross_paise=gross,
        paid_paise=gross,
        tender=dict(
            cash_paise=reservation.payable_paise,
            voucher_paise=reservation.voucher_paise,
            rewards_paise=quote["reward_discount_paise"],
        ),
    )
